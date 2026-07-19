from __future__ import annotations

import json
import io
import hashlib
import hmac
import os
import secrets
import smtplib
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "crm_erp.db"
INDEX_PATH = BASE_DIR / "app" / "index.html"
UPLOAD_DIR = BASE_DIR / "uploads"
CV_DIR = UPLOAD_DIR / "cv"
DOCUMENT_DIR = UPLOAD_DIR / "documents"

CLIENT_STATUSES = (
    "Nowy lead", "Kontakt", "W rozmowie", "Aktywny klient", "Zakwalifikowany",
    "Niezainteresowany", "Nieaktywny klient", "Skontaktowano",
)
PRIORITIES = ("Niski", "Średni", "Wysoki")
REALIZATION_STATUSES = ("Gotowe do terminu", "DC", "PC", "AC", "WP", "Gotowe do sprawdzenia", "Puste")
DEAL_STAGES = ("Rozpoznanie", "Zakwalifikowane", "Oferta", "Negocjacje", "Gotowe do montażu", "Zamknięte", "Utracone")
STAGE_PROBABILITIES = {
    "Rozpoznanie": 0.10,
    "Zakwalifikowane": 0.20,
    "Oferta": 0.50,
    "Negocjacje": 0.75,
    "Gotowe do montażu": 0.95,
    "Zamknięte": 1.0,
    "Utracone": 0.0,
}
SESSION_COOKIE = "nexa_session"
SESSION_TTL_DAYS = 7
LOGIN_ATTEMPTS: dict[str, list[float]] = {}


def get_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="minutes")


def init_db() -> None:
    CV_DIR.mkdir(parents=True, exist_ok=True)
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    with get_db() as db:
        db.executescript(
            """
            PRAGMA journal_mode = WAL;

            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT NOT NULL,
                contact_name TEXT NOT NULL,
                contact_email TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                owner TEXT NOT NULL,
                last_contact TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                priority TEXT NOT NULL DEFAULT 'Średni',
                estimated_value REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                project_name TEXT NOT NULL,
                priority TEXT NOT NULL,
                realization_status TEXT NOT NULL,
                deal_stage TEXT NOT NULL DEFAULT 'Rozpoznanie',
                responsible TEXT NOT NULL,
                salesperson TEXT NOT NULL DEFAULT '',
                place TEXT NOT NULL DEFAULT '',
                deal_date TEXT NOT NULL DEFAULT '',
                value_pln REAL NOT NULL DEFAULT 0,
                materials_cost REAL NOT NULL DEFAULT 0,
                commission_rate REAL NOT NULL DEFAULT 5,
                paid_percent INTEGER NOT NULL DEFAULT 0 CHECK (paid_percent BETWEEN 0 AND 100),
                material_ordered INTEGER NOT NULL DEFAULT 0 CHECK (material_ordered IN (0, 1)),
                document_url TEXT NOT NULL DEFAULT '',
                product_items TEXT NOT NULL DEFAULT '[]',
                start_deadline TEXT NOT NULL DEFAULT '',
                end_deadline TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS time_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                employee_email TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                duration_seconds INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                client_id INTEGER,
                owner_email TEXT NOT NULL,
                starts_at TEXT,
                duration_minutes INTEGER NOT NULL DEFAULT 60,
                notes TEXT NOT NULL DEFAULT '',
                google_event_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                system_role TEXT NOT NULL DEFAULT 'User',
                position TEXT NOT NULL,
                manager TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT 'Warszawa',
                start_date TEXT NOT NULL,
                employee_status TEXT NOT NULL DEFAULT 'Aktywny',
                phone TEXT NOT NULL DEFAULT '',
                bio TEXT NOT NULL DEFAULT '',
                photo_url TEXT NOT NULL DEFAULT '',
                password_hash TEXT NOT NULL DEFAULT '',
                password_salt TEXT NOT NULL DEFAULT '',
                must_change_password INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS auth_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                sku TEXT NOT NULL UNIQUE,
                wholesale_price REAL NOT NULL,
                suggested_price REAL NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                channel TEXT NOT NULL,
                direction TEXT NOT NULL,
                content TEXT NOT NULL,
                employee_email TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                assigned_to TEXT NOT NULL,
                deadline TEXT NOT NULL DEFAULT '',
                task_status TEXT NOT NULL DEFAULT 'Otwarte',
                created_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                recipient TEXT NOT NULL,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS processes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                position TEXT NOT NULL,
                candidate_status TEXT NOT NULL DEFAULT 'Nowy',
                rating INTEGER NOT NULL DEFAULT 0,
                cv_filename TEXT NOT NULL DEFAULT '',
                cv_stored_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                filename TEXT NOT NULL DEFAULT '',
                stored_name TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_clients_status ON clients(status);
            CREATE INDEX IF NOT EXISTS idx_projects_client ON projects(client_id);
            CREATE INDEX IF NOT EXISTS idx_projects_priority ON projects(priority);
            CREATE INDEX IF NOT EXISTS idx_projects_stage ON projects(deal_stage);
            CREATE INDEX IF NOT EXISTS idx_time_project ON time_sessions(project_id);
            CREATE INDEX IF NOT EXISTS idx_messages_client ON messages(client_id);
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token_hash);
            """
        )

        client_count = db.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        if client_count == 0:
            seed_database(db)
        seed_supporting_data(db)
        ensure_auth_schema(db)


def seed_database(db: sqlite3.Connection) -> None:
    clients = [
        ("HelioNova Sp. z o.o.", "Marta Wiśniewska", "marta@helionova.pl", "Zakwalifikowany", "Jakub Wójcik", "2026-07-14T09:42+02:00", ["Średnia firma", "PV"]),
        ("Nordhaus Development", "Tomasz Kamiński", "t.kaminski@nordhaus.pl", "Skontaktowano", "Anna Kowalska", "2026-07-13T16:18+02:00", ["MF", "PC"]),
        ("EcoVolt Solutions", "Aleksandra Nowak", "a.nowak@ecovolt.pl", "Nowy lead", "Jakub Wójcik", "2026-07-12T11:05+02:00", ["PV", "Nowy"]),
        ("Greenpoint Logistics", "Piotr Zieliński", "p.zielinski@greenpoint.pl", "Zakwalifikowany", "Michał Lewandowski", "2026-07-11T14:30+02:00", ["Średnia firma"]),
        ("Vistula Property", "Katarzyna Dąbrowska", "k.dabrowska@vistula.pl", "Skontaktowano", "Anna Kowalska", "2026-07-10T10:15+02:00", ["MF", "PV"]),
    ]
    created = now_iso()
    db.executemany(
        """
        INSERT INTO clients (company, contact_name, contact_email, status, owner, last_contact, tags, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(*client[:6], json.dumps(client[6], ensure_ascii=False), created) for client in clients],
    )

    client_ids = {
        row["company"]: row["id"]
        for row in db.execute("SELECT id, company FROM clients").fetchall()
    }
    projects = [
        (client_ids["HelioNova Sp. z o.o."], "PV + pompa ciepła", "Wysoki", "Gotowe do terminu", "Oferta", "marta.krol@nexa.pl", "Anna Kowalska", 185000, 106000, 6.0, 68, 1, "https://drive.google.com/"),
        (client_ids["Nordhaus Development"], "New Deal / Etap II", "Średni", "Gotowe do sprawdzenia", "Negocjacje", "piotr.mazur@nexa.pl", "Piotr Mazur", 320000, 205000, 5.0, 42, 1, "https://drive.google.com/"),
        (client_ids["EcoVolt Solutions"], "Instalacja PV 50 kWp", "Wysoki", "DC", "Zakwalifikowane", "jakub.wojcik@nexa.pl", "Jakub Wójcik", 126000, 79000, 5.5, 12, 0, ""),
        (client_ids["Greenpoint Logistics"], "Modernizacja magazynu", "Niski", "AC", "Zamknięte", "anna.kowalska@nexa.pl", "Anna Kowalska", 94000, 54000, 4.0, 86, 1, "https://drive.google.com/"),
        (client_ids["Vistula Property"], "Audyt energetyczny", "Średni", "Gotowe do sprawdzenia", "Rozpoznanie", "michal.lewandowski@nexa.pl", "Michał Lewandowski", 48000, 11000, 5.0, 10, 0, "https://drive.google.com/"),
    ]
    db.executemany(
        """
        INSERT INTO projects (
            client_id, project_name, priority, realization_status, deal_stage,
            responsible, salesperson, value_pln, materials_cost, commission_rate,
            paid_percent, material_ordered, document_url, deal_date, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(*project, "2026-07-14", created) for project in projects],
    )

    db.execute(
        "UPDATE clients SET phone = ?, address = ?, priority = ?, estimated_value = ? WHERE company = ?",
        ("+48 500 110 220", "ul. Słoneczna 14, Warszawa", "Wysoki", 185000, "HelioNova Sp. z o.o."),
    )
    db.execute(
        "UPDATE clients SET phone = ?, address = ?, priority = ?, estimated_value = ? WHERE company = ?",
        ("+48 502 330 440", "al. Północna 8, Gdańsk", "Średni", 320000, "Nordhaus Development"),
    )


def seed_supporting_data(db: sqlite3.Connection) -> None:
    created = now_iso()
    if db.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0:
        db.executemany(
            """
            INSERT INTO employees
            (name, email, system_role, position, manager, location, start_date, employee_status, phone, bio)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("Anna Kowalska", "anna.kowalska@nexa.pl", "Admin", "Sales Director", "", "Warszawa", "2023-02-01", "Aktywny", "+48 501 100 100", "Odpowiada za sprzedaż i rozwój zespołu."),
                ("Jakub Wójcik", "jakub.wojcik@nexa.pl", "User", "Account Executive", "Anna Kowalska", "Kraków", "2024-01-15", "Aktywny", "+48 501 100 101", "Sprzedaż instalacji dla sektora MŚP."),
                ("Piotr Mazur", "piotr.mazur@nexa.pl", "User", "Project Manager", "Anna Kowalska", "Gdańsk", "2023-09-04", "Aktywny", "+48 501 100 102", "Koordynacja montaży i logistyki."),
                ("Marta Król", "marta.krol@nexa.pl", "User", "Technical Lead", "Piotr Mazur", "Warszawa", "2024-03-18", "Aktywny", "+48 501 100 103", "Dokumentacja techniczna i odbiory."),
                ("Michał Lewandowski", "michal.lewandowski@nexa.pl", "User", "Sales Specialist", "Anna Kowalska", "Poznań", "2025-02-03", "Aktywny", "+48 501 100 104", "Obsługa leadów i audytów."),
            ],
        )
    if db.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        db.executemany(
            "INSERT INTO products (name, sku, wholesale_price, suggested_price) VALUES (?, ?, ?, ?)",
            [
                ("Panel fotowoltaiczny 450 W", "PV-450-BLK", 520, 790),
                ("Falownik hybrydowy 10 kW", "INV-HYB-10", 8900, 12900),
                ("Magazyn energii 10 kWh", "BAT-LFP-10", 16400, 23900),
                ("Pompa ciepła 12 kW", "PC-AIR-12", 21800, 31900),
                ("Konstrukcja i okablowanie", "KIT-MOUNT-01", 6200, 9800),
            ],
        )
    if db.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 0:
        client_rows = db.execute("SELECT id, company FROM clients ORDER BY id").fetchall()
        meetings = [
            ("Prezentacja oferty PV", client_rows[0]["id"], "anna.kowalska@nexa.pl", "2026-07-14T10:00:00+02:00", 60, "Omówienie wariantów finansowania."),
            ("Kick-off techniczny", client_rows[1]["id"], "piotr.mazur@nexa.pl", "2026-07-17T13:30:00+02:00", 90, "Zakres Etapu II."),
            ("Audyt lokalizacji", client_rows[2]["id"], "jakub.wojcik@nexa.pl", "2026-07-22T09:00:00+02:00", 120, "Wizja lokalna."),
            ("Follow-up bez terminu", client_rows[3]["id"], "anna.kowalska@nexa.pl", None, 30, "Ustalić osobę decyzyjną."),
        ]
        db.executemany(
            "INSERT INTO meetings (title, client_id, owner_email, starts_at, duration_minutes, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(*item, created) for item in meetings],
        )
    if db.execute("SELECT COUNT(*) FROM processes").fetchone()[0] == 0:
        db.executemany(
            "INSERT INTO processes (title, category, content, sort_order, updated_at) VALUES (?, ?, ?, ?, ?)",
            [
                ("Hiring Process", "HR", "1. Zatwierdź wakat\n2. Opublikuj ogłoszenie\n3. Screening CV\n4. Rozmowa\n5. Oferta", 1, created),
                ("Client Onboarding", "Sprzedaż", "1. Podpisz umowę\n2. Załóż folder projektu\n3. Zbierz dokumenty\n4. Przekaż do realizacji", 2, created),
                ("Odbiór instalacji", "Realizacja", "1. Checklist techniczny\n2. Zdjęcia\n3. Protokół\n4. Faktura końcowa", 3, created),
            ],
        )
    if db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0:
        db.executemany(
            "INSERT INTO documents (title, category, content, updated_at) VALUES (?, ?, ?, ?)",
            [
                ("Szablon oferty handlowej", "Szablony", "Zakres, harmonogram, warunki płatności i ważność oferty.", created),
                ("Przewodnik kwalifikacji leada", "Przewodniki", "Budżet · Decyzyjność · Potrzeba · Termin.", created),
                ("Standard bezpieczeństwa danych", "Dostęp", "Dokumenty klientów udostępniamy wyłącznie osobom przypisanym do projektu.", created),
            ],
        )
    if db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0:
        first_client = db.execute("SELECT id FROM clients ORDER BY id LIMIT 1").fetchone()
        if first_client:
            db.executemany(
                "INSERT INTO messages (client_id, channel, direction, content, employee_email, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (first_client["id"], "email", "in", "Dziękuję za ofertę. Proszę o wariant z magazynem energii.", "anna.kowalska@nexa.pl", "2026-07-13T08:40:00+02:00"),
                    (first_client["id"], "sms", "out", "Wysłałam kalkulację. Porozmawiajmy jutro o 10:00.", "anna.kowalska@nexa.pl", "2026-07-13T09:05:00+02:00"),
                ],
            )


def password_digest(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        310_000,
    ).hex()


def ensure_auth_schema(db: sqlite3.Connection) -> None:
    columns = {row["name"] for row in db.execute("PRAGMA table_info(employees)").fetchall()}
    if "password_hash" not in columns:
        db.execute("ALTER TABLE employees ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")
    if "password_salt" not in columns:
        db.execute("ALTER TABLE employees ADD COLUMN password_salt TEXT NOT NULL DEFAULT ''")
    if "must_change_password" not in columns:
        db.execute("ALTER TABLE employees ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 1")

    initial_password = os.getenv("NEXA_INITIAL_PASSWORD", "Nexa2026!")
    employees = db.execute(
        "SELECT id FROM employees WHERE password_hash = '' OR password_salt = ''"
    ).fetchall()
    for employee in employees:
        salt = secrets.token_hex(16)
        db.execute(
            "UPDATE employees SET password_hash = ?, password_salt = ?, must_change_password = 1 WHERE id = ?",
            (password_digest(initial_password, salt), salt, employee["id"]),
        )
    db.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now_iso(),))


def public_employee(row: sqlite3.Row | dict) -> dict:
    result = dict(row)
    result.pop("password_hash", None)
    result.pop("password_salt", None)
    return result


def decode_client(row: sqlite3.Row) -> dict:
    result = dict(row)
    try:
        result["tags"] = json.loads(result.get("tags", "[]"))
    except json.JSONDecodeError:
        result["tags"] = []
    return result


def decode_project(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["material_ordered"] = bool(result["material_ordered"])
    try:
        result["product_items"] = json.loads(result.get("product_items", "[]"))
    except json.JSONDecodeError:
        result["product_items"] = []
    result["profit"] = round(float(result.get("value_pln", 0)) - float(result.get("materials_cost", 0)), 2)
    result["commission_due"] = round(result["profit"] * float(result.get("commission_rate", 0)) / 100, 2)
    return result


class ClientCreate(BaseModel):
    company: str = Field(min_length=2, max_length=120)
    contact_name: str = Field(min_length=2, max_length=120)
    contact_email: str = Field(default="", max_length=160)
    phone: str = Field(default="", max_length=40)
    address: str = Field(default="", max_length=300)
    status: str = "Nowy lead"
    owner: str = Field(min_length=2, max_length=120)
    last_contact: str | None = None
    tags: list[str] = Field(default_factory=list, max_length=8)
    priority: str = "Średni"
    estimated_value: float = Field(default=0, ge=0)
    notes: str = Field(default="", max_length=10000)

    @field_validator("company", "contact_name", "owner")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, tags: list[str]) -> list[str]:
        return list(dict.fromkeys(tag.strip()[:32] for tag in tags if tag.strip()))

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in CLIENT_STATUSES:
            raise ValueError("Nieprawidłowy status klienta")
        return value

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, value: str) -> str:
        if value not in PRIORITIES:
            raise ValueError("Nieprawidłowy priorytet")
        return value


class ClientUpdate(BaseModel):
    company: str | None = Field(default=None, min_length=2, max_length=120)
    contact_name: str | None = Field(default=None, min_length=2, max_length=120)
    contact_email: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=300)
    status: str | None = None
    owner: str | None = Field(default=None, min_length=2, max_length=120)
    last_contact: str | None = None
    tags: list[str] | None = Field(default=None, max_length=8)
    priority: str | None = None
    estimated_value: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=10000)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is not None and value not in CLIENT_STATUSES:
            raise ValueError("Nieprawidłowy status klienta")
        return value

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, value: str | None) -> str | None:
        if value is not None and value not in PRIORITIES:
            raise ValueError("Nieprawidłowy priorytet")
        return value


class ProjectCreate(BaseModel):
    client_id: int = Field(gt=0)
    project_name: str = Field(min_length=2, max_length=160)
    priority: str = "Średni"
    realization_status: str = "Puste"
    deal_stage: str = "Rozpoznanie"
    responsible: str = Field(min_length=2, max_length=160)
    salesperson: str = Field(default="", max_length=160)
    place: str = Field(default="", max_length=200)
    deal_date: str = ""
    value_pln: float = Field(default=0, ge=0)
    materials_cost: float = Field(default=0, ge=0)
    commission_rate: float = Field(default=5, ge=0, le=100)
    paid_percent: int = Field(default=0, ge=0, le=100)
    material_ordered: bool = False
    document_url: str = Field(default="", max_length=500)
    product_items: list[dict] = Field(default_factory=list)
    start_deadline: str = ""
    end_deadline: str = ""

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, value: str) -> str:
        if value not in PRIORITIES:
            raise ValueError("Nieprawidłowy priorytet")
        return value

    @field_validator("realization_status")
    @classmethod
    def validate_realization(cls, value: str) -> str:
        if value not in REALIZATION_STATUSES:
            raise ValueError("Nieprawidłowy status realizacji")
        return value

    @field_validator("deal_stage")
    @classmethod
    def validate_stage(cls, value: str) -> str:
        if value not in DEAL_STAGES:
            raise ValueError("Nieprawidłowy etap transakcji")
        return value


class ProjectUpdate(BaseModel):
    client_id: int | None = Field(default=None, gt=0)
    project_name: str | None = Field(default=None, min_length=2, max_length=160)
    priority: str | None = None
    realization_status: str | None = None
    deal_stage: str | None = None
    responsible: str | None = Field(default=None, min_length=2, max_length=160)
    salesperson: str | None = Field(default=None, max_length=160)
    place: str | None = Field(default=None, max_length=200)
    deal_date: str | None = None
    value_pln: float | None = Field(default=None, ge=0)
    materials_cost: float | None = Field(default=None, ge=0)
    commission_rate: float | None = Field(default=None, ge=0, le=100)
    paid_percent: int | None = Field(default=None, ge=0, le=100)
    material_ordered: bool | None = None
    document_url: str | None = Field(default=None, max_length=500)
    product_items: list[dict] | None = None
    start_deadline: str | None = None
    end_deadline: str | None = None

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, value: str | None) -> str | None:
        if value is not None and value not in PRIORITIES:
            raise ValueError("Nieprawidłowy priorytet")
        return value

    @field_validator("realization_status")
    @classmethod
    def validate_realization(cls, value: str | None) -> str | None:
        if value is not None and value not in REALIZATION_STATUSES:
            raise ValueError("Nieprawidłowy status realizacji")
        return value

    @field_validator("deal_stage")
    @classmethod
    def validate_stage(cls, value: str | None) -> str | None:
        if value is not None and value not in DEAL_STAGES:
            raise ValueError("Nieprawidłowy etap transakcji")
        return value


class MeetingCreate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    client_id: int | None = None
    owner_email: str = Field(min_length=3, max_length=160)
    starts_at: str | None = None
    duration_minutes: int = Field(default=60, ge=15, le=1440)
    notes: str = Field(default="", max_length=4000)


class MeetingUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    client_id: int | None = None
    owner_email: str | None = Field(default=None, max_length=160)
    starts_at: str | None = None
    duration_minutes: int | None = Field(default=None, ge=15, le=1440)
    notes: str | None = Field(default=None, max_length=4000)


class MessageCreate(BaseModel):
    channel: Literal["email", "sms"] = "email"
    content: str = Field(min_length=1, max_length=5000)
    employee_email: str = Field(default="anna.kowalska@nexa.pl", max_length=160)


class ProductCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    sku: str = Field(default="", max_length=80)
    wholesale_price: float = Field(default=0, ge=0)
    suggested_price: float = Field(default=0, ge=0)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    sku: str | None = Field(default=None, max_length=80)
    wholesale_price: float | None = Field(default=None, ge=0)
    suggested_price: float | None = Field(default=None, ge=0)
    active: bool | None = None


class PublicLeadCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    phone: str = Field(min_length=7, max_length=40)
    email: str = Field(default="", max_length=160)
    street: str = Field(default="", max_length=200)
    postal_code: str = Field(default="", max_length=12)
    city: str = Field(default="", max_length=120)
    service: str = Field(default="", max_length=400)
    services: list[str] = Field(default_factory=list)
    building_type: str = Field(default="", max_length=120)
    area_m2: float | None = None
    current_heating: str = Field(default="", max_length=120)
    monthly_bill: float | None = None
    pv_kwp: float | None = None
    storage_kwh: float | None = None
    annual_consumption_kwh: float | None = None
    estimate_low: float | None = None
    estimate_high: float | None = None
    investment_time: str = Field(default="", max_length=120)
    preferred_contact: str = Field(default="", max_length=120)
    message: str = Field(default="", max_length=4000)
    source: str = Field(default="", max_length=120)
    consent: bool = False
    website: str = Field(default="", max_length=200)


class RoleUpdate(BaseModel):
    system_role: Literal["Admin", "User"]


class TextUpdate(BaseModel):
    content: str = Field(max_length=50000)


class CandidateUpdate(BaseModel):
    candidate_status: str | None = Field(default=None, max_length=80)
    rating: int | None = Field(default=None, ge=0, le=5)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=160)
    password: str = Field(min_length=8, max_length=256)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=8, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)

    @field_validator("new_password")
    @classmethod
    def strong_password(cls, value: str) -> str:
        if not any(character.isupper() for character in value):
            raise ValueError("Nowe hasło musi zawierać wielką literę")
        if not any(character.islower() for character in value):
            raise ValueError("Nowe hasło musi zawierać małą literę")
        if not any(character.isdigit() for character in value):
            raise ValueError("Nowe hasło musi zawierać cyfrę")
        return value


def session_user_or_none(request: Request, db: sqlite3.Connection) -> dict | None:
    if os.getenv("NEXA_ALLOW_DEV_HEADER") == "1":
        dev_email = request.headers.get("X-User-Email")
        if dev_email:
            dev_user = db.execute("SELECT * FROM employees WHERE email = ?", (dev_email,)).fetchone()
            return public_employee(dev_user) if dev_user else None

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = db.execute(
        """
        SELECT e.* FROM auth_sessions s
        JOIN employees e ON e.id = s.employee_id
        WHERE s.token_hash = ? AND s.expires_at > ? AND e.employee_status = 'Aktywny'
        """,
        (token_hash, now_iso()),
    ).fetchone()
    return public_employee(row) if row else None


def resolve_user(request: Request, db: sqlite3.Connection) -> dict:
    user = session_user_or_none(request, db)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Zaloguj się, aby uzyskać dostęp do CRM",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return user


def require_admin(user: dict) -> None:
    if user.get("system_role") != "Admin":
        raise HTTPException(status_code=403, detail="Ta operacja wymaga roli Admin")


def can_access_client(user: dict, row: sqlite3.Row | dict) -> bool:
    if user.get("system_role") == "Admin":
        return True
    data = dict(row)
    return data.get("owner") in {user.get("name"), user.get("email")}


def can_access_project(user: dict, row: sqlite3.Row | dict) -> bool:
    if user.get("system_role") == "Admin":
        return True
    data = dict(row)
    return (
        data.get("responsible") in {user.get("name"), user.get("email")}
        or data.get("salesperson") in {user.get("name"), user.get("email")}
        or data.get("client_owner") in {user.get("name"), user.get("email")}
    )


def setting_value(db: sqlite3.Connection, key: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def save_setting(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def safe_upload_name(original_name: str, allowed_suffixes: set[str]) -> tuple[str, str]:
    original = Path(original_name or "plik").name
    suffix = Path(original).suffix.lower()
    if suffix not in allowed_suffixes:
        raise HTTPException(status_code=400, detail=f"Dozwolone formaty: {', '.join(sorted(allowed_suffixes))}")
    return original, f"{uuid.uuid4().hex}{suffix}"


def refresh_google_token(db: sqlite3.Connection) -> str:
    """Odświeża access token Google przy pomocy refresh tokena. Zwraca nowy token lub ''."""
    refresh_token = setting_value(db, "google_refresh_token")
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not refresh_token or not client_id or not client_secret:
        return ""
    body = urllib.parse.urlencode(
        {
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    token_request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(token_request, timeout=15) as token_response:
            token_data = json.loads(token_response.read().decode("utf-8"))
        new_token = token_data.get("access_token", "")
        if new_token:
            save_setting(db, "google_access_token", new_token)
        return new_token
    except Exception:
        return ""


def google_calendar_request(db: sqlite3.Connection, url: str, method: str, payload: bytes | None) -> dict:
    """Wykonuje żądanie do Google Calendar API z automatycznym odświeżeniem tokena po 401."""
    token = setting_value(db, "google_access_token")
    if not token:
        token = refresh_google_token(db)
    if not token:
        return {}
    for attempt in range(2):
        calendar_request = urllib.request.Request(
            url,
            data=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(calendar_request, timeout=10) as google_response:
                raw = google_response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 0:
                token = refresh_google_token(db)
                if not token:
                    return {}
                continue
            return {}
        except Exception:
            return {}
    return {}


def google_event_payload(meeting: dict) -> bytes | None:
    if not meeting.get("starts_at"):
        return None
    try:
        start = datetime.fromisoformat(meeting["starts_at"])
    except ValueError:
        return None
    end = start + timedelta(minutes=int(meeting.get("duration_minutes", 60)))
    return json.dumps(
        {
            "summary": meeting["title"],
            "description": meeting.get("notes", ""),
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
    ).encode("utf-8")


def sync_google_meeting(db: sqlite3.Connection, meeting: dict) -> str:
    """Tworzy lub aktualizuje wydarzenie w Google Calendar. Zwraca ID wydarzenia."""
    payload = google_event_payload(meeting)
    if payload is None:
        return meeting.get("google_event_id", "") or ""
    event_id = meeting.get("google_event_id", "") or ""
    if event_id:
        result = google_calendar_request(
            db,
            f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{urllib.parse.quote(event_id)}",
            "PATCH",
            payload,
        )
        return result.get("id", event_id)
    result = google_calendar_request(
        db, "https://www.googleapis.com/calendar/v3/calendars/primary/events", "POST", payload
    )
    return result.get("id", "")


def delete_google_meeting(db: sqlite3.Connection, event_id: str) -> None:
    if not event_id:
        return
    google_calendar_request(
        db,
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{urllib.parse.quote(event_id)}",
        "DELETE",
        None,
    )


def send_candidate_status_email(email: str, name: str, candidate_status: str) -> bool:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    if not smtp_host or not smtp_user or not smtp_password:
        return False
    message = EmailMessage()
    message["Subject"] = f"Aktualizacja rekrutacji Nexa: {candidate_status}"
    message["From"] = smtp_user
    message["To"] = email
    message.set_content(
        f"Dzień dobry {name},\n\nstatus Twojej kandydatury został zmieniony na: {candidate_status}.\n\nZespół Nexa"
    )
    with smtplib.SMTP_SSL(smtp_host, int(os.getenv("SMTP_PORT", "465")), timeout=10) as smtp:
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(message)
    return True


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Nexa CRM & ERP",
    description="Zintegrowany system sprzedaży, realizacji, finansów i operacji.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://greenexperts.pl",
        "https://www.greenexperts.pl",
        "https://crm.greenexperts.pl",
    ],
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/", include_in_schema=False)
def dashboard(request: Request) -> Response:
    with get_db() as db:
        if not session_user_or_none(request, db):
            return RedirectResponse("/login", status_code=303)
    return FileResponse(INDEX_PATH, media_type="text/html")


@app.get("/login", include_in_schema=False)
def login_page(request: Request) -> Response:
    with get_db() as db:
        if session_user_or_none(request, db):
            return RedirectResponse("/", status_code=303)
    return FileResponse(INDEX_PATH, media_type="text/html")


@app.get("/health", tags=["System"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/login", tags=["Autoryzacja"])
def login(payload: LoginRequest, response: Response) -> dict:
    email = payload.email.strip().lower()
    current_time = time.time()
    recent_attempts = [
        attempt for attempt in LOGIN_ATTEMPTS.get(email, [])
        if current_time - attempt < 300
    ]
    if len(recent_attempts) >= 5:
        raise HTTPException(
            status_code=429,
            detail="Zbyt wiele prób logowania. Spróbuj ponownie za kilka minut.",
        )
    with get_db() as db:
        employee = db.execute(
            "SELECT * FROM employees WHERE lower(email) = ? AND employee_status = 'Aktywny'",
            (email,),
        ).fetchone()
        password_valid = bool(
            employee
            and employee["password_salt"]
            and hmac.compare_digest(
                employee["password_hash"],
                password_digest(payload.password, employee["password_salt"]),
            )
        )
        if not password_valid:
            recent_attempts.append(current_time)
            LOGIN_ATTEMPTS[email] = recent_attempts
            raise HTTPException(status_code=401, detail="Nieprawidłowy e-mail lub hasło")

        LOGIN_ATTEMPTS.pop(email, None)
        raw_token = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        expires_at = (
            datetime.now().astimezone() + timedelta(days=SESSION_TTL_DAYS)
        ).isoformat(timespec="minutes")
        db.execute(
            "INSERT INTO auth_sessions (employee_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (employee["id"], token_hash, expires_at, now_iso()),
        )
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=SESSION_TTL_DAYS * 86400,
        httponly=True,
        secure=os.getenv("NEXA_COOKIE_SECURE") == "1",
        samesite="strict",
        path="/",
    )
    return {"user": public_employee(employee), "expires_at": expires_at}


@app.post("/api/auth/logout", tags=["Autoryzacja"])
def logout(request: Request, response: Response) -> dict[str, bool]:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with get_db() as db:
            db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict")
    return {"logged_out": True}


@app.post("/api/auth/change-password", tags=["Autoryzacja"])
def change_password(payload: PasswordChange, request: Request, response: Response) -> dict[str, bool]:
    with get_db() as db:
        user = resolve_user(request, db)
        employee = db.execute("SELECT * FROM employees WHERE id = ?", (user["id"],)).fetchone()
        if not employee or not hmac.compare_digest(
            employee["password_hash"],
            password_digest(payload.current_password, employee["password_salt"]),
        ):
            raise HTTPException(status_code=400, detail="Obecne hasło jest nieprawidłowe")
        salt = secrets.token_hex(16)
        db.execute(
            "UPDATE employees SET password_hash = ?, password_salt = ?, must_change_password = 0 WHERE id = ?",
            (password_digest(payload.new_password, salt), salt, user["id"]),
        )
        db.execute("DELETE FROM auth_sessions WHERE employee_id = ?", (user["id"],))
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict")
    return {"password_changed": True}


@app.get("/api/session", tags=["System"])
def session_info(request: Request) -> dict:
    with get_db() as db:
        return resolve_user(request, db)


@app.get("/api/clients", tags=["CRM"])
def list_clients(
    request: Request,
    search: str = Query(default="", max_length=100),
    client_status: str | None = Query(default=None, alias="status"),
    view: str = Query(default="all", pattern="^(all|priority|high_value)$"),
) -> list[dict]:
    clauses: list[str] = []
    params: list[str | float] = []
    if search.strip():
        clauses.append("(company LIKE ? OR contact_name LIKE ? OR owner LIKE ?)")
        term = f"%{search.strip()}%"
        params.extend([term, term, term])
    if client_status:
        if client_status not in CLIENT_STATUSES:
            raise HTTPException(status_code=400, detail="Nieprawidłowy status klienta")
        clauses.append("status = ?")
        params.append(client_status)
    if view == "high_value":
        clauses.append(
            "(estimated_value >= 100000 OR EXISTS "
            "(SELECT 1 FROM projects p WHERE p.client_id = clients.id AND p.value_pln >= 100000))"
        )

    with get_db() as db:
        user = resolve_user(request, db)
        if user["system_role"] != "Admin":
            clauses.append("(owner = ? OR owner = ?)")
            params.extend([user["name"], user["email"]])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order = (
            "CASE priority WHEN 'Wysoki' THEN 1 WHEN 'Średni' THEN 2 ELSE 3 END, estimated_value DESC"
            if view == "priority"
            else "last_contact DESC, id DESC"
        )
        rows = db.execute(
            f"SELECT * FROM clients {where} ORDER BY {order}", params
        ).fetchall()
    return [decode_client(row) for row in rows]


@app.post("/api/clients", tags=["CRM"], status_code=status.HTTP_201_CREATED)
def create_client(payload: ClientCreate, request: Request) -> dict:
    with get_db() as db:
        user = resolve_user(request, db)
        owner = payload.owner if user["system_role"] == "Admin" else user["name"]
        cursor = db.execute(
            """
            INSERT INTO clients (
                company, contact_name, contact_email, phone, address, status, owner,
                last_contact, tags, priority, estimated_value, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.company,
                payload.contact_name,
                payload.contact_email.strip(),
                payload.phone.strip(),
                payload.address.strip(),
                payload.status,
                owner,
                payload.last_contact or now_iso(),
                json.dumps(payload.tags, ensure_ascii=False),
                payload.priority,
                payload.estimated_value,
                payload.notes,
                now_iso(),
            ),
        )
        row = db.execute("SELECT * FROM clients WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return decode_client(row)


@app.patch("/api/clients/{client_id}", tags=["CRM"])
def update_client(client_id: int, payload: ClientUpdate, request: Request) -> dict:
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "tags" in changes:
        changes["tags"] = json.dumps(changes["tags"], ensure_ascii=False)
    if not changes:
        raise HTTPException(status_code=400, detail="Brak danych do aktualizacji")
    with get_db() as db:
        user = resolve_user(request, db)
        exists = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Nie znaleziono klienta")
        if not can_access_client(user, exists):
            raise HTTPException(status_code=403, detail="Brak dostępu do tego klienta")
        if user["system_role"] != "Admin":
            changes.pop("owner", None)
        if not changes:
            raise HTTPException(status_code=403, detail="Brak pól dostępnych do edycji")
        assignments = ", ".join(f"{field} = ?" for field in changes)
        db.execute(
            f"UPDATE clients SET {assignments} WHERE id = ?",
            (*changes.values(), client_id),
        )
        row = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    return decode_client(row)


@app.delete("/api/clients/{client_id}", tags=["CRM"], status_code=status.HTTP_204_NO_CONTENT)
def delete_client(client_id: int, request: Request) -> Response:
    with get_db() as db:
        user = resolve_user(request, db)
        require_admin(user)
        cursor = db.execute("DELETE FROM clients WHERE id = ?", (client_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Nie znaleziono klienta")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/projects", tags=["Realizacja"])
def list_projects(
    request: Request,
    search: str = Query(default="", max_length=100),
    priority: str | None = None,
    deal_stage: str | None = None,
    realization_status: str | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list[str] = []
    if search.strip():
        clauses.append("(p.project_name LIKE ? OR c.company LIKE ? OR p.responsible LIKE ?)")
        term = f"%{search.strip()}%"
        params.extend([term, term, term])
    if priority:
        if priority not in PRIORITIES:
            raise HTTPException(status_code=400, detail="Nieprawidłowy priorytet")
        clauses.append("p.priority = ?")
        params.append(priority)
    if deal_stage:
        if deal_stage not in DEAL_STAGES:
            raise HTTPException(status_code=400, detail="Nieprawidłowy etap transakcji")
        clauses.append("p.deal_stage = ?")
        params.append(deal_stage)
    if realization_status:
        if realization_status not in REALIZATION_STATUSES:
            raise HTTPException(status_code=400, detail="Nieprawidłowy status realizacji")
        clauses.append("p.realization_status = ?")
        params.append(realization_status)
    with get_db() as db:
        user = resolve_user(request, db)
        if user["system_role"] != "Admin":
            clauses.append(
                "(p.responsible IN (?, ?) OR p.salesperson IN (?, ?) OR c.owner IN (?, ?))"
            )
            params.extend([user["name"], user["email"], user["name"], user["email"], user["name"], user["email"]])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = db.execute(
            f"""
            SELECT p.*, c.company AS client_name, c.owner AS client_owner,
                   COALESCE((SELECT SUM(duration_seconds) FROM time_sessions t WHERE t.project_id = p.id), 0) AS total_seconds
            FROM projects p
            JOIN clients c ON c.id = p.client_id
            {where}
            ORDER BY CASE p.priority WHEN 'Wysoki' THEN 1 WHEN 'Średni' THEN 2 ELSE 3 END, p.id DESC
            """,
            params,
        ).fetchall()
    return [decode_project(row) for row in rows]


@app.post("/api/projects", tags=["Realizacja"], status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, request: Request) -> dict:
    with get_db() as db:
        user = resolve_user(request, db)
        client = db.execute("SELECT * FROM clients WHERE id = ?", (payload.client_id,)).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Nie znaleziono wybranego klienta")
        if not can_access_client(user, client):
            raise HTTPException(status_code=403, detail="Brak dostępu do wybranego klienta")
        salesperson = payload.salesperson or user["email"]
        responsible = payload.responsible if user["system_role"] == "Admin" else user["email"]
        cursor = db.execute(
            """
            INSERT INTO projects (
                client_id, project_name, priority, realization_status, deal_stage,
                responsible, salesperson, place, deal_date, value_pln, materials_cost,
                commission_rate, paid_percent, material_ordered, document_url,
                product_items, start_deadline, end_deadline, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.client_id,
                payload.project_name.strip(),
                payload.priority,
                payload.realization_status,
                payload.deal_stage,
                responsible.strip(),
                salesperson.strip(),
                payload.place.strip(),
                payload.deal_date or datetime.now().date().isoformat(),
                payload.value_pln,
                payload.materials_cost,
                payload.commission_rate,
                payload.paid_percent,
                int(payload.material_ordered),
                payload.document_url.strip(),
                json.dumps(payload.product_items, ensure_ascii=False),
                payload.start_deadline,
                payload.end_deadline,
                now_iso(),
            ),
        )
        row = db.execute(
            """
            SELECT p.*, c.company AS client_name, c.owner AS client_owner FROM projects p
            JOIN clients c ON c.id = p.client_id WHERE p.id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return decode_project(row)


@app.patch("/api/projects/{project_id}", tags=["Realizacja"])
def update_project(project_id: int, payload: ProjectUpdate, request: Request) -> dict:
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "material_ordered" in changes:
        changes["material_ordered"] = int(changes["material_ordered"])
    if "product_items" in changes:
        changes["product_items"] = json.dumps(changes["product_items"], ensure_ascii=False)
    if not changes:
        raise HTTPException(status_code=400, detail="Brak danych do aktualizacji")
    with get_db() as db:
        user = resolve_user(request, db)
        exists = db.execute(
            """
            SELECT p.*, c.owner AS client_owner FROM projects p
            JOIN clients c ON c.id = p.client_id WHERE p.id = ?
            """,
            (project_id,),
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Nie znaleziono projektu")
        if not can_access_project(user, exists):
            raise HTTPException(status_code=403, detail="Brak dostępu do tego projektu")
        if "client_id" in changes:
            client = db.execute("SELECT * FROM clients WHERE id = ?", (changes["client_id"],)).fetchone()
            if not client:
                raise HTTPException(status_code=404, detail="Nie znaleziono wybranego klienta")
            if not can_access_client(user, client):
                raise HTTPException(status_code=403, detail="Brak dostępu do wybranego klienta")
        if user["system_role"] != "Admin":
            changes.pop("commission_rate", None)
            changes.pop("salesperson", None)
        if not changes:
            raise HTTPException(status_code=403, detail="Brak pól dostępnych do edycji")
        assignments = ", ".join(f"{field} = ?" for field in changes)
        db.execute(
            f"UPDATE projects SET {assignments} WHERE id = ?",
            (*changes.values(), project_id),
        )
        if changes.get("deal_stage") == "Oferta" and exists["deal_stage"] != "Oferta":
            deadline = (datetime.now().date() + timedelta(days=3)).isoformat()
            db.execute(
                "INSERT INTO tasks (project_id, title, assigned_to, deadline, created_at) VALUES (?, ?, ?, ?, ?)",
                (project_id, "Zadzwoń i domknij ofertę", exists["salesperson"] or exists["responsible"], deadline, now_iso()),
            )
        if changes.get("deal_stage") == "Gotowe do montażu" and exists["deal_stage"] != "Gotowe do montażu":
            db.execute(
                "INSERT INTO notifications (category, title, content, recipient, created_at) VALUES (?, ?, ?, ?, ?)",
                ("Magazyn", "Skompletuj materiały", f"Projekt {exists['project_name']} jest gotowy do montażu.", "magazyn@nexa.pl", now_iso()),
            )
        row = db.execute(
            """
            SELECT p.*, c.company AS client_name, c.owner AS client_owner,
                   COALESCE((SELECT SUM(duration_seconds) FROM time_sessions t WHERE t.project_id = p.id), 0) AS total_seconds
            FROM projects p
            JOIN clients c ON c.id = p.client_id WHERE p.id = ?
            """,
            (project_id,),
        ).fetchone()
    return decode_project(row)


@app.delete("/api/projects/{project_id}", tags=["Realizacja"], status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, request: Request) -> Response:
    with get_db() as db:
        user = resolve_user(request, db)
        require_admin(user)
        cursor = db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Nie znaleziono projektu")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/summary", tags=["Dashboard"])
def summary(request: Request) -> dict:
    with get_db() as db:
        user = resolve_user(request, db)
        client_where = ""
        client_params: list[str] = []
        project_where = ""
        project_params: list[str] = []
        if user["system_role"] != "Admin":
            client_where = " WHERE owner IN (?, ?)"
            client_params = [user["name"], user["email"]]
            project_where = (
                " WHERE p.responsible IN (?, ?) OR p.salesperson IN (?, ?) OR c.owner IN (?, ?)"
            )
            project_params = [user["name"], user["email"], user["name"], user["email"], user["name"], user["email"]]
        clients = db.execute(f"SELECT COUNT(*) FROM clients{client_where}", client_params).fetchone()[0]
        qualified = db.execute(
            f"SELECT COUNT(*) FROM clients {client_where + (' AND' if client_where else ' WHERE')} status = 'Zakwalifikowany'",
            client_params,
        ).fetchone()[0]
        project_base = " FROM projects p JOIN clients c ON c.id = p.client_id"
        projects = db.execute(f"SELECT COUNT(*){project_base}{project_where}", project_params).fetchone()[0]
        high_priority = db.execute(
            f"SELECT COUNT(*){project_base}{project_where + (' AND' if project_where else ' WHERE')} p.priority = 'Wysoki'",
            project_params,
        ).fetchone()[0]
        average_paid = db.execute(
            f"SELECT COALESCE(ROUND(AVG(p.paid_percent)), 0){project_base}{project_where}",
            project_params,
        ).fetchone()[0]
        scoped_rows = db.execute(
            f"SELECT p.value_pln, p.deal_stage{project_base}{project_where}",
            project_params,
        ).fetchall()
        forecast = round(sum(row["value_pln"] * STAGE_PROBABILITIES.get(row["deal_stage"], 0) for row in scoped_rows), 2)
    return {
        "clients": clients,
        "qualified": qualified,
        "projects": projects,
        "high_priority": high_priority,
        "average_paid": average_paid,
        "forecast": forecast,
        "role": user["system_role"],
        "user": user["name"],
    }


@app.get("/api/products", tags=["CPQ"])
def list_products(request: Request, include_inactive: bool = False) -> list[dict]:
    with get_db() as db:
        resolve_user(request, db)
        query = "SELECT * FROM products ORDER BY active DESC, name" if include_inactive else "SELECT * FROM products WHERE active = 1 ORDER BY name"
        return [dict(row) for row in db.execute(query).fetchall()]


@app.post("/api/products", tags=["CPQ"], status_code=201)
def create_product(payload: ProductCreate, request: Request) -> dict:
    with get_db() as db:
        resolve_user(request, db)
        sku = payload.sku.strip() or f"SKU-{uuid.uuid4().hex[:8].upper()}"
        try:
            cursor = db.execute(
                "INSERT INTO products (name, sku, wholesale_price, suggested_price, active) VALUES (?, ?, ?, ?, 1)",
                (payload.name.strip(), sku, payload.wholesale_price, payload.suggested_price),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Produkt z takim SKU już istnieje")
        row = db.execute("SELECT * FROM products WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


@app.patch("/api/products/{product_id}", tags=["CPQ"])
def update_product(product_id: int, payload: ProductUpdate, request: Request) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Brak danych do aktualizacji")
    if "active" in changes:
        changes["active"] = 1 if changes["active"] else 0
    with get_db() as db:
        resolve_user(request, db)
        exists = db.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Nie znaleziono produktu")
        assignments = ", ".join(f"{field} = ?" for field in changes)
        try:
            db.execute(f"UPDATE products SET {assignments} WHERE id = ?", (*changes.values(), product_id))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Produkt z takim SKU już istnieje")
        row = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return dict(row)


@app.delete("/api/products/{product_id}", tags=["CPQ"], status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, request: Request) -> Response:
    with get_db() as db:
        resolve_user(request, db)
        cursor = db.execute("UPDATE products SET active = 0 WHERE id = ?", (product_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Nie znaleziono produktu")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/public/leads", tags=["Public"], status_code=201)
def create_public_lead(payload: PublicLeadCreate) -> dict:
    if payload.website.strip():
        return {"status": "ok"}
    if not payload.consent:
        raise HTTPException(status_code=422, detail="Wymagana zgoda na przetwarzanie danych")
    address_parts = [part for part in (payload.street.strip(), f"{payload.postal_code} {payload.city}".strip()) if part]
    address = ", ".join(address_parts)
    details = []
    if payload.building_type:
        details.append(f"Budynek: {payload.building_type}")
    if payload.area_m2:
        details.append(f"Powierzchnia: {payload.area_m2} m2")
    if payload.current_heating:
        details.append(f"Ogrzewanie: {payload.current_heating}")
    if payload.monthly_bill:
        details.append(f"Rachunek/mc: {payload.monthly_bill} zl")
    if payload.pv_kwp:
        details.append(f"PV: {payload.pv_kwp} kWp")
    if payload.storage_kwh:
        details.append(f"Magazyn: {payload.storage_kwh} kWh")
    if payload.annual_consumption_kwh:
        details.append(f"Zuzycie roczne: {payload.annual_consumption_kwh} kWh")
    if payload.estimate_low or payload.estimate_high:
        details.append(f"Szacunek: {payload.estimate_low or '?'} - {payload.estimate_high or '?'} zl")
    if payload.investment_time:
        details.append(f"Termin inwestycji: {payload.investment_time}")
    if payload.preferred_contact:
        details.append(f"Preferowany kontakt: {payload.preferred_contact}")
    if payload.message:
        details.append(f"Wiadomosc: {payload.message}")
    notes = "\n".join(details)
    estimated = payload.estimate_high or payload.estimate_low or 0
    with get_db() as db:
        cursor = db.execute(
            """
            INSERT INTO clients (company, contact_name, contact_email, phone, address, status, owner, last_contact, tags, priority, estimated_value, notes, created_at)
            VALUES (?, ?, ?, ?, ?, 'Nowy lead', '', ?, ?, 'Średni', ?, ?, ?)
            """,
            (
                payload.name.strip(),
                payload.name.strip(),
                payload.email.strip(),
                payload.phone.strip(),
                address,
                now_iso(),
                json.dumps([tag for tag in payload.services if tag][:6], ensure_ascii=False),
                float(estimated or 0),
                notes,
                now_iso(),
            ),
        )
        db.execute(
            "INSERT INTO notifications (title, content, category, recipient, created_at) VALUES (?, ?, 'Lead', '', ?)",
            (
                f"Nowy lead ze strony: {payload.name.strip()}",
                f"Uslugi: {payload.service or ', '.join(payload.services)} | Tel: {payload.phone}",
                now_iso(),
            ),
        )
    return {"status": "ok", "id": cursor.lastrowid}


@app.get("/api/clients/{client_id}/messages", tags=["Komunikacja"])
def client_messages(client_id: int, request: Request) -> list[dict]:
    with get_db() as db:
        user = resolve_user(request, db)
        client = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Nie znaleziono klienta")
        if not can_access_client(user, client):
            raise HTTPException(status_code=403, detail="Brak dostępu do komunikacji klienta")
        rows = db.execute(
            "SELECT * FROM messages WHERE client_id = ? ORDER BY created_at DESC, id DESC",
            (client_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/clients/{client_id}/messages", tags=["Komunikacja"], status_code=201)
def send_client_message(client_id: int, payload: MessageCreate, request: Request) -> dict:
    with get_db() as db:
        user = resolve_user(request, db)
        client = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Nie znaleziono klienta")
        if not can_access_client(user, client):
            raise HTTPException(status_code=403, detail="Brak dostępu do komunikacji klienta")
        cursor = db.execute(
            """
            INSERT INTO messages (client_id, channel, direction, content, employee_email, created_at)
            VALUES (?, ?, 'out', ?, ?, ?)
            """,
            (client_id, payload.channel, payload.content, user["email"], now_iso()),
        )
        row = db.execute("SELECT * FROM messages WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


@app.post("/api/clients/{client_id}/calls/start", tags=["Komunikacja"], status_code=201)
def start_client_call(client_id: int, request: Request) -> dict:
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    with get_db() as db:
        user = resolve_user(request, db)
        client = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Nie znaleziono klienta")
        if not can_access_client(user, client):
            raise HTTPException(status_code=403, detail="Brak dostępu do klienta")
        cursor = db.execute(
            """
            INSERT INTO messages (client_id, channel, direction, content, employee_email, created_at)
            VALUES (?, 'call', 'out', 'Połączenie wychodzące', ?, ?)
            """,
            (client_id, user["email"], started),
        )
    return {"id": cursor.lastrowid, "started_at": started, "phone": client["phone"]}


@app.post("/api/clients/{client_id}/calls/{call_id}/stop", tags=["Komunikacja"])
def stop_client_call(client_id: int, call_id: int, request: Request) -> dict:
    ended = datetime.now().astimezone()
    with get_db() as db:
        user = resolve_user(request, db)
        row = db.execute(
            "SELECT * FROM messages WHERE id = ? AND client_id = ? AND channel = 'call'",
            (call_id, client_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Nie znaleziono połączenia")
        if row["employee_email"] != user["email"] and user["system_role"] != "Admin":
            raise HTTPException(status_code=403, detail="Brak dostępu do połączenia")
        duration = max(1, int((ended - datetime.fromisoformat(row["created_at"])).total_seconds()))
        db.execute(
            "UPDATE messages SET duration_seconds = ?, content = ? WHERE id = ?",
            (duration, f"Połączenie wychodzące · {duration} s", call_id),
        )
    return {"id": call_id, "duration_seconds": duration}


@app.get("/api/time-sessions", tags=["Czas pracy"])
def list_time_sessions(request: Request, period: str = "all") -> list[dict]:
    clauses: list[str] = []
    params: list[str] = []
    if period == "today":
        clauses.append("date(t.started_at) = date('now', 'localtime')")
    with get_db() as db:
        user = resolve_user(request, db)
        if user["system_role"] != "Admin":
            clauses.append("t.employee_email = ?")
            params.append(user["email"])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = db.execute(
            f"""
            SELECT t.*, p.project_name, c.company AS client_name
            FROM time_sessions t
            JOIN projects p ON p.id = t.project_id
            JOIN clients c ON c.id = p.client_id
            {where}
            ORDER BY t.started_at DESC
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/projects/{project_id}/timer/toggle", tags=["Czas pracy"])
def toggle_project_timer(project_id: int, request: Request) -> dict:
    current_time = datetime.now().astimezone()
    current_iso = current_time.isoformat(timespec="seconds")
    with get_db() as db:
        user = resolve_user(request, db)
        project = db.execute(
            """
            SELECT p.*, c.owner AS client_owner FROM projects p
            JOIN clients c ON c.id = p.client_id WHERE p.id = ?
            """,
            (project_id,),
        ).fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Nie znaleziono projektu")
        if not can_access_project(user, project):
            raise HTTPException(status_code=403, detail="Brak dostępu do projektu")
        active = db.execute(
            "SELECT * FROM time_sessions WHERE project_id = ? AND employee_email = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
            (project_id, user["email"]),
        ).fetchone()
        if active:
            duration = max(1, int((current_time - datetime.fromisoformat(active["started_at"])).total_seconds()))
            db.execute(
                "UPDATE time_sessions SET ended_at = ?, duration_seconds = ? WHERE id = ?",
                (current_iso, duration, active["id"]),
            )
            return {"running": False, "session_id": active["id"], "duration_seconds": duration}
        cursor = db.execute(
            "INSERT INTO time_sessions (project_id, employee_email, started_at) VALUES (?, ?, ?)",
            (project_id, user["email"], current_iso),
        )
        return {"running": True, "session_id": cursor.lastrowid, "started_at": current_iso}


@app.get("/api/meetings", tags=["Spotkania"])
def list_meetings(request: Request, month: str | None = None) -> list[dict]:
    clauses: list[str] = []
    params: list[str] = []
    if month:
        clauses.append("(starts_at IS NULL OR substr(starts_at, 1, 7) = ?)")
        params.append(month)
    with get_db() as db:
        user = resolve_user(request, db)
        if user["system_role"] != "Admin":
            clauses.append("owner_email = ?")
            params.append(user["email"])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = db.execute(
            f"""
            SELECT m.*, c.company AS client_name FROM meetings m
            LEFT JOIN clients c ON c.id = m.client_id
            {where} ORDER BY starts_at IS NULL, starts_at
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/meetings", tags=["Spotkania"], status_code=201)
def create_meeting(payload: MeetingCreate, request: Request) -> dict:
    with get_db() as db:
        user = resolve_user(request, db)
        owner = payload.owner_email if user["system_role"] == "Admin" else user["email"]
        cursor = db.execute(
            """
            INSERT INTO meetings (title, client_id, owner_email, starts_at, duration_minutes, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (payload.title, payload.client_id, owner, payload.starts_at, payload.duration_minutes, payload.notes, now_iso()),
        )
        row = db.execute("SELECT * FROM meetings WHERE id = ?", (cursor.lastrowid,)).fetchone()
        event_id = sync_google_meeting(db, dict(row))
        if event_id:
            db.execute("UPDATE meetings SET google_event_id = ? WHERE id = ?", (event_id, row["id"]))
        result = db.execute(
            "SELECT m.*, c.company AS client_name FROM meetings m LEFT JOIN clients c ON c.id = m.client_id WHERE m.id = ?",
            (row["id"],),
        ).fetchone()
    return dict(result)


@app.patch("/api/meetings/{meeting_id}", tags=["Spotkania"])
def update_meeting(meeting_id: int, payload: MeetingUpdate, request: Request) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Brak danych do aktualizacji")
    with get_db() as db:
        user = resolve_user(request, db)
        meeting = db.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if not meeting:
            raise HTTPException(status_code=404, detail="Nie znaleziono spotkania")
        if user["system_role"] != "Admin" and meeting["owner_email"] != user["email"]:
            raise HTTPException(status_code=403, detail="Brak dostępu do spotkania")
        if user["system_role"] != "Admin":
            changes.pop("owner_email", None)
        assignments = ", ".join(f"{field} = ?" for field in changes)
        db.execute(f"UPDATE meetings SET {assignments} WHERE id = ?", (*changes.values(), meeting_id))
        updated = db.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        event_id = sync_google_meeting(db, dict(updated))
        if event_id and event_id != updated["google_event_id"]:
            db.execute("UPDATE meetings SET google_event_id = ? WHERE id = ?", (event_id, meeting_id))
        row = db.execute(
            "SELECT m.*, c.company AS client_name FROM meetings m LEFT JOIN clients c ON c.id = m.client_id WHERE m.id = ?",
            (meeting_id,),
        ).fetchone()
    return dict(row)


@app.delete("/api/meetings/{meeting_id}", tags=["Spotkania"], status_code=status.HTTP_204_NO_CONTENT)
def delete_meeting(meeting_id: int, request: Request) -> Response:
    with get_db() as db:
        user = resolve_user(request, db)
        meeting = db.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if not meeting:
            raise HTTPException(status_code=404, detail="Nie znaleziono spotkania")
        if user["system_role"] != "Admin" and meeting["owner_email"] != user["email"]:
            raise HTTPException(status_code=403, detail="Brak dostępu do spotkania")
        delete_google_meeting(db, meeting["google_event_id"])
        db.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/finance", tags=["Finanse"])
def finance_dashboard(request: Request) -> dict:
    with get_db() as db:
        user = resolve_user(request, db)
        params: list[str] = []
        where = ""
        if user["system_role"] != "Admin":
            where = "WHERE p.deal_stage = 'Zamknięte' AND p.salesperson IN (?, ?)"
            params = [user["name"], user["email"]]
        rows = db.execute(
            f"""
            SELECT p.*, c.company AS client_name FROM projects p
            JOIN clients c ON c.id = p.client_id
            {where} ORDER BY p.deal_date DESC, p.id DESC
            """,
            params,
        ).fetchall()
        projects = [decode_project(row) for row in rows]
        if user["system_role"] != "Admin":
            for project in projects:
                project.pop("materials_cost", None)
                project.pop("profit", None)
        total_revenue = round(sum(float(row["value_pln"]) for row in rows), 2)
        total_materials = round(sum(float(row["materials_cost"]) for row in rows), 2)
        total_commission = round(sum((float(row["value_pln"]) - float(row["materials_cost"])) * float(row["commission_rate"]) / 100 for row in rows), 2)
        total_profit = round(total_revenue - total_materials - total_commission, 2)
        open_rows = db.execute(
            """
            SELECT value_pln, deal_stage FROM projects
            WHERE deal_stage NOT IN ('Zamknięte', 'Utracone')
            """
        ).fetchall()
        forecast = round(sum(row["value_pln"] * STAGE_PROBABILITIES.get(row["deal_stage"], 0) for row in open_rows), 2)
    return {
        "role": user["system_role"],
        "user": user["name"],
        "projects": projects,
        "kpi": {
            "revenue": total_revenue,
            "materials": total_materials if user["system_role"] == "Admin" else None,
            "commission": total_commission,
            "operating_profit": total_profit if user["system_role"] == "Admin" else None,
            "forecast": forecast,
        },
        "chart": {
            "labels": ["Materiały", "Prowizje", "Zysk operacyjny"],
            "values": [total_materials, total_commission, max(0, total_profit)],
        } if user["system_role"] == "Admin" else None,
    }


@app.get("/api/finance/performance", tags=["Finanse"])
def sales_performance(request: Request) -> list[dict]:
    with get_db() as db:
        user = resolve_user(request, db)
        employees = db.execute(
            "SELECT name, email FROM employees WHERE position LIKE '%Sales%' OR position LIKE '%Account%' ORDER BY name"
        ).fetchall()
        result = []
        for employee in employees:
            if user["system_role"] != "Admin" and employee["email"] != user["email"]:
                continue
            touches = db.execute(
                "SELECT COUNT(*) FROM messages WHERE employee_email = ?",
                (employee["email"],),
            ).fetchone()[0]
            leads = db.execute(
                "SELECT COUNT(*) FROM clients WHERE owner IN (?, ?)",
                (employee["name"], employee["email"]),
            ).fetchone()[0]
            wins = db.execute(
                "SELECT COUNT(*) FROM projects WHERE salesperson IN (?, ?) AND deal_stage = 'Zamknięte'",
                (employee["name"], employee["email"]),
            ).fetchone()[0]
            result.append({
                "name": employee["name"],
                "email": employee["email"],
                "touches": touches,
                "conversion": round((wins / leads * 100) if leads else 0, 1),
                "wins": wins,
            })
    return result


@app.get("/api/projects/{project_id}/invoice.pdf", tags=["Finanse"])
def project_invoice(project_id: int, request: Request) -> StreamingResponse:
    with get_db() as db:
        user = resolve_user(request, db)
        row = db.execute(
            """
            SELECT p.*, c.company AS client_name, c.address AS client_address, c.owner AS client_owner
            FROM projects p JOIN clients c ON c.id = p.client_id WHERE p.id = ?
            """,
            (project_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Nie znaleziono projektu")
        if not can_access_project(user, row):
            raise HTTPException(status_code=403, detail="Brak dostępu do faktury")
        project = dict(row)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Brak biblioteki reportlab") from exc
    buffer = io.BytesIO()
    font_name = "Helvetica"
    for font_path in (Path("C:/Windows/Fonts/arial.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")):
        if font_path.exists():
            pdfmetrics.registerFont(TTFont("NexaFont", str(font_path)))
            font_name = "NexaFont"
            break
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    pdf.setFillColorRGB(0.08, 0.08, 0.1)
    pdf.rect(0, height - 48 * mm, width, 48 * mm, fill=1, stroke=0)
    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont(font_name, 22)
    pdf.drawString(20 * mm, height - 25 * mm, "NEXA · FAKTURA PRO FORMA")
    pdf.setFont(font_name, 9)
    pdf.drawString(20 * mm, height - 35 * mm, f"Numer: NEXA/{datetime.now().year}/{project_id:04d}")
    pdf.setFillColorRGB(0.12, 0.12, 0.14)
    y = height - 67 * mm
    pdf.setFont(font_name, 11)
    pdf.drawString(20 * mm, y, f"Nabywca: {project['client_name']}")
    pdf.setFont(font_name, 9)
    pdf.drawString(20 * mm, y - 7 * mm, project.get("client_address") or "Adres nieuzupełniony")
    pdf.line(20 * mm, y - 18 * mm, width - 20 * mm, y - 18 * mm)
    pdf.setFont(font_name, 10)
    pdf.drawString(20 * mm, y - 30 * mm, "Projekt")
    pdf.drawString(120 * mm, y - 30 * mm, "Wartość brutto")
    pdf.setFont(font_name, 11)
    pdf.drawString(20 * mm, y - 40 * mm, project["project_name"])
    pdf.drawRightString(width - 20 * mm, y - 40 * mm, f"{project['value_pln']:,.2f} PLN".replace(",", " "))
    pdf.line(20 * mm, y - 50 * mm, width - 20 * mm, y - 50 * mm)
    pdf.setFont(font_name, 9)
    pdf.drawString(20 * mm, y - 63 * mm, f"Zapłacono: {project['paid_percent']}%")
    pdf.drawRightString(width - 20 * mm, y - 63 * mm, f"Do zapłaty: {project['value_pln'] * (100-project['paid_percent'])/100:,.2f} PLN".replace(",", " "))
    pdf.setFillColorRGB(0.4, 0.4, 0.45)
    pdf.drawString(20 * mm, 18 * mm, "Dokument wygenerowany automatycznie przez Nexa CRM & ERP.")
    pdf.save()
    buffer.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="faktura-NEXA-{project_id:04d}.pdf"'}
    return StreamingResponse(buffer, media_type="application/pdf", headers=headers)


@app.get("/api/employees", tags=["Zespół"])
def list_employees(request: Request) -> list[dict]:
    with get_db() as db:
        resolve_user(request, db)
        rows = db.execute(
            """
            SELECT id, name, email, system_role, position, manager, location, start_date,
                   employee_status, phone, bio, photo_url, must_change_password
            FROM employees ORDER BY employee_status, name
            """
        ).fetchall()
        return [dict(row) for row in rows]


@app.patch("/api/employees/{employee_id}/role", tags=["Zespół"])
def update_employee_role(employee_id: int, payload: RoleUpdate, request: Request) -> dict:
    with get_db() as db:
        user = resolve_user(request, db)
        require_admin(user)
        db.execute("UPDATE employees SET system_role = ? WHERE id = ?", (payload.system_role, employee_id))
        row = db.execute(
            """
            SELECT id, name, email, system_role, position, manager, location, start_date,
                   employee_status, phone, bio, photo_url, must_change_password
            FROM employees WHERE id = ?
            """,
            (employee_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Nie znaleziono pracownika")
    return dict(row)


@app.get("/api/processes", tags=["Operacje"])
def list_processes(request: Request) -> list[dict]:
    with get_db() as db:
        resolve_user(request, db)
        return [dict(row) for row in db.execute("SELECT * FROM processes ORDER BY sort_order, title").fetchall()]


@app.patch("/api/processes/{process_id}", tags=["Operacje"])
def update_process(process_id: int, payload: TextUpdate, request: Request) -> dict:
    with get_db() as db:
        user = resolve_user(request, db)
        require_admin(user)
        db.execute("UPDATE processes SET content = ?, updated_at = ? WHERE id = ?", (payload.content, now_iso(), process_id))
        row = db.execute("SELECT * FROM processes WHERE id = ?", (process_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Nie znaleziono procesu")
    return dict(row)


@app.get("/api/candidates", tags=["ATS"])
def list_candidates(request: Request) -> list[dict]:
    with get_db() as db:
        user = resolve_user(request, db)
        require_admin(user)
        return [dict(row) for row in db.execute("SELECT * FROM candidates ORDER BY created_at DESC").fetchall()]


@app.post("/api/candidates", tags=["ATS"], status_code=201)
async def upload_candidate(
    name: str = Form(...),
    email: str = Form(...),
    position: str = Form(...),
    cv: UploadFile = File(...),
) -> dict:
    original, stored = safe_upload_name(cv.filename or "cv.pdf", {".pdf"})
    content = await cv.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CV może mieć maksymalnie 8 MB")
    (CV_DIR / stored).write_bytes(content)
    with get_db() as db:
        cursor = db.execute(
            """
            INSERT INTO candidates (name, email, position, cv_filename, cv_stored_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name.strip(), email.strip(), position.strip(), original, stored, now_iso()),
        )
        row = db.execute("SELECT * FROM candidates WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


@app.patch("/api/candidates/{candidate_id}", tags=["ATS"])
def update_candidate(candidate_id: int, payload: CandidateUpdate, request: Request) -> dict:
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Brak danych do aktualizacji")
    with get_db() as db:
        user = resolve_user(request, db)
        require_admin(user)
        candidate = db.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        if not candidate:
            raise HTTPException(status_code=404, detail="Nie znaleziono kandydata")
        assignments = ", ".join(f"{field} = ?" for field in changes)
        db.execute(f"UPDATE candidates SET {assignments} WHERE id = ?", (*changes.values(), candidate_id))
        if "candidate_status" in changes and changes["candidate_status"] != candidate["candidate_status"]:
            email_sent = False
            try:
                email_sent = send_candidate_status_email(candidate["email"], candidate["name"], changes["candidate_status"])
            except Exception:
                email_sent = False
            db.execute(
                "INSERT INTO notifications (category, title, content, recipient, created_at) VALUES (?, ?, ?, ?, ?)",
                ("ATS", "Zmiana statusu kandydata", f"{candidate['name']}: {changes['candidate_status']} · email={'wysłany' if email_sent else 'oczekuje na SMTP'}", user["email"], now_iso()),
            )
        row = db.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    return dict(row)


@app.get("/api/candidates/{candidate_id}/cv", tags=["ATS"])
def download_candidate_cv(candidate_id: int, request: Request) -> FileResponse:
    with get_db() as db:
        user = resolve_user(request, db)
        require_admin(user)
        row = db.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        if not row or not row["cv_stored_name"]:
            raise HTTPException(status_code=404, detail="Brak pliku CV")
    path = CV_DIR / row["cv_stored_name"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Plik CV nie istnieje")
    return FileResponse(path, media_type="application/pdf", filename=row["cv_filename"])


@app.get("/api/documents", tags=["Dokumenty"])
def list_documents(request: Request) -> list[dict]:
    with get_db() as db:
        resolve_user(request, db)
        return [dict(row) for row in db.execute("SELECT * FROM documents ORDER BY category, title").fetchall()]


@app.patch("/api/documents/{document_id}", tags=["Dokumenty"])
def update_document(document_id: int, payload: TextUpdate, request: Request) -> dict:
    with get_db() as db:
        user = resolve_user(request, db)
        require_admin(user)
        db.execute("UPDATE documents SET content = ?, updated_at = ? WHERE id = ?", (payload.content, now_iso(), document_id))
        row = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Nie znaleziono dokumentu")
    return dict(row)


@app.post("/api/documents/upload", tags=["Dokumenty"], status_code=201)
async def upload_document(
    request: Request,
    title: str = Form(...),
    category: str = Form("Repozytorium"),
    file: UploadFile = File(...),
) -> dict:
    with get_db() as db:
        user = resolve_user(request, db)
        require_admin(user)
    original, stored = safe_upload_name(file.filename or "dokument.pdf", {".pdf"})
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Dokument może mieć maksymalnie 20 MB")
    (DOCUMENT_DIR / stored).write_bytes(content)
    with get_db() as db:
        cursor = db.execute(
            "INSERT INTO documents (title, category, filename, stored_name, updated_at) VALUES (?, ?, ?, ?, ?)",
            (title.strip(), category.strip(), original, stored, now_iso()),
        )
        row = db.execute("SELECT * FROM documents WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


@app.get("/api/documents/{document_id}/download", tags=["Dokumenty"])
def download_document(document_id: int, request: Request) -> FileResponse:
    with get_db() as db:
        resolve_user(request, db)
        row = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if not row or not row["stored_name"]:
            raise HTTPException(status_code=404, detail="Brak pliku dokumentu")
    path = DOCUMENT_DIR / row["stored_name"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Plik dokumentu nie istnieje")
    return FileResponse(path, media_type="application/pdf", filename=row["filename"])


@app.get("/api/notifications", tags=["Automatyzacje"])
def list_notifications(request: Request) -> list[dict]:
    with get_db() as db:
        user = resolve_user(request, db)
        if user["system_role"] == "Admin":
            rows = db.execute("SELECT * FROM notifications ORDER BY created_at DESC, id DESC LIMIT 50").fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM notifications WHERE recipient IN (?, ?) ORDER BY created_at DESC, id DESC LIMIT 50",
                (user["email"], user["name"]),
            ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/tasks", tags=["Automatyzacje"])
def list_tasks(request: Request) -> list[dict]:
    with get_db() as db:
        user = resolve_user(request, db)
        if user["system_role"] == "Admin":
            rows = db.execute(
                "SELECT t.*, p.project_name FROM tasks t JOIN projects p ON p.id = t.project_id ORDER BY t.created_at DESC"
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT t.*, p.project_name FROM tasks t JOIN projects p ON p.id = t.project_id WHERE t.assigned_to IN (?, ?) ORDER BY t.created_at DESC",
                (user["email"], user["name"]),
            ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/integrations/google/status", tags=["Integracje"])
def google_status(request: Request) -> dict:
    configured = bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))
    with get_db() as db:
        require_admin(resolve_user(request, db))
        connected = bool(setting_value(db, "google_access_token"))
    return {"configured": configured, "connected": connected}


@app.get("/auth/google", tags=["Integracje"], include_in_schema=False)
def google_auth(request: Request) -> RedirectResponse:
    with get_db() as db:
        require_admin(resolve_user(request, db))
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8010/auth/google/callback")
    if not client_id:
        raise HTTPException(status_code=503, detail="Ustaw GOOGLE_CLIENT_ID i GOOGLE_CLIENT_SECRET")
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/calendar.events",
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{query}")


@app.get("/auth/google/callback", tags=["Integracje"], include_in_schema=False)
def google_callback(code: str, request: Request) -> RedirectResponse:
    with get_db() as db:
        require_admin(resolve_user(request, db))
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8010/auth/google/callback")
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="Brak konfiguracji OAuth2")
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    token_request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(token_request, timeout=15) as token_response:
            token_data = json.loads(token_response.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Google OAuth2 nie zwrócił tokenu") from exc
    with get_db() as db:
        save_setting(db, "google_access_token", token_data.get("access_token", ""))
        if token_data.get("refresh_token"):
            save_setting(db, "google_refresh_token", token_data["refresh_token"])
    return RedirectResponse("/?google=connected")
