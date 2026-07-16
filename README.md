# Nexa CRM & ERP Advanced

Zaawansowana, jednoplikowa aplikacja FastAPI + SQLite z interfejsem SPA w czystym HTML, Tailwind CSS i JavaScript.

## Struktura

- `app/main.py` — cała logika backendu, schemat bazy, dane demonstracyjne i API,
- `app/index.html` — cały interfejs, widoki, Kanbany i obsługa JS,
- `crm_erp.db` — baza SQLite tworzona automatycznie przy pierwszym uruchomieniu,
- `uploads/` — prywatne pliki CV i dokumenty tworzone podczas pracy aplikacji.

## Instalacja i uruchomienie

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8010
```

Jeżeli środowisko `.venv` już istnieje:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8010
```

Dashboard: http://127.0.0.1:8010  
Dokumentacja API: http://127.0.0.1:8010/docs

## Moduły

- CRM: kontakty, priorytety, filtr wartości ≥ 100 tys. PLN i Kanban statusów,
- transakcje: tabela z sumowaniem oraz lejek Kanban z automatyzacjami,
- spotkania: kalendarz miesięczny i integracja OAuth2 Google Calendar,
- realizacja: techniczny Kanban i Client Portals,
- czas pracy: trwałe sesje Start/Stop przypisane do pracownika i projektu,
- finanse: koszty, zysk, prowizje, ważony forecast, KPI sprzedawcy, wykresy i faktury PDF,
- operacje: zespół/RBAC, SOP, ATS z uploadem CV, cele, workflow, dokumenty i Wiki,
- klient 360°: notatki auto-save, Unified Inbox oraz rejestrowane połączenia VoIP w trybie symulacji,
- CPQ: katalog produktów, automatyczne koszty materiałów i sugerowana wartość zlecenia.

## Role i konfiguracja

Wejście do CRM wymaga logowania. Hasła są zapisywane jako PBKDF2-SHA256 z indywidualną solą, a losowy token sesji trafia do ciasteczka `HttpOnly` na 7 dni. Wylogowanie usuwa sesję z bazy.

Konto startowe:

- e-mail: `anna.kowalska@nexa.pl`
- hasło: `Nexa2026!`

Hasło startowe dla nowej bazy można ustawić przed pierwszym uruchomieniem:

```powershell
$env:NEXA_INITIAL_PASSWORD="TwojeMocneHaslo2026!"
$env:NEXA_COOKIE_SECURE="1"  # włącz przy produkcyjnym HTTPS
```

Filtrowanie RBAC odbywa się w zapytaniach backendu. Administrator widzi całość, a User wyłącznie rekordy przypisane do jego nazwiska lub e-maila. Nagłówek testowy `X-User-Email` jest domyślnie wyłączony; można go świadomie włączyć wyłącznie lokalnie przez `NEXA_ALLOW_DEV_HEADER=1`.

Google Calendar OAuth2:

```powershell
$env:GOOGLE_CLIENT_ID="..."
$env:GOOGLE_CLIENT_SECRET="..."
$env:GOOGLE_REDIRECT_URI="http://127.0.0.1:8010/auth/google/callback"
```

Powiadomienia ATS przez SMTP są wysyłane po ustawieniu: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER` i `SMTP_PASSWORD`. Bez konfiguracji zdarzenie jest bezpiecznie zapisywane w powiadomieniach systemowych.
