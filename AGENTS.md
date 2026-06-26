# AGENTS Guide for DRFBoilerplate

This file documents how to work inside this Django REST Framework boilerplate.

## 1) Project layout

- `server/` is the Django project root (run management commands from here).
- `server/server/` contains Django project config:
  - `settings.py`: installed apps, auth, JWT, email, CORS, templates.
  - `urls.py`: global routes for auth and app includes.
  - `asgi.py`, `wsgi.py`: deployment entry points.
- `server/administrator/` is the main custom app right now:
  - `models.py`: custom `User` model extending `AbstractUser`.
  - `auth_views.py`: custom login and password-change API views.
  - `serializers.py`: shared serializers and custom DRF exception handler.
  - `urls.py`: app URL routes (currently empty).
- `server/all_auth_extended/all_auth_extended.py` customizes allauth email links to frontend URLs.
- `server/utils/` holds shared utilities:
  - `shared.py`: shared pagination class.
  - `email_handler.py`: threaded HTML email sender with optional image/PDF attachments.
- `server/templates/account/email/` overrides allauth email templates.

## 2) Runtime and dependencies

- Python/Django stack:
  - `Django==5.2`
  - `djangorestframework==3.14.0`
  - `dj-rest-auth`, `django-allauth`, `simplejwt`
- Default DB is SQLite (`db.sqlite3`), with commented PostgreSQL example in settings.
- JWT is the default auth mechanism (`DEFAULT_AUTHENTICATION_CLASSES` uses `JWTAuthentication`).
- CORS is currently fully open in settings (`CORS_ALLOW_ALL_ORIGINS = True`) for development.

## 3) Existing API/auth patterns

- Auth endpoints live under `rest-auth/` in `server/server/urls.py`.
- Custom login view (`LoginWthPermission`) manually authenticates user and returns:
  - `success`, `access`, `refresh`, `user`, `role`.
- Password change uses a custom serializer and view (`CustomPasswordChangeSerializer`, `CustomPasswordChangeView`).
- Global DRF exception formatting is centralized through:
  - `REST_FRAMEWORK["EXCEPTION_HANDLER"] = "administrator.serializers.custom_exception_handler"`
  - This wraps errors as `{"success": False, "message": "..."}`.

## 4) Coding style observed in this repo

- Class-based DRF views (`APIView`) are preferred in examples and existing auth code. So use APIView class for writing new views unless a specific reason exists to use other class-based views (e.g., `ListAPIView`, `RetrieveAPIView`) or function-based views.
- Response payloads usually include explicit success/error semantics (`success`, `message`, `detail`).
- Shared concerns are extracted into `utils/` (pagination, email).
- Imports are typically grouped by framework then local modules.
- Codebase currently has minimal tests and lightweight app `urls.py` stubs.

## 5) Conventions to keep when adding features

- New app APIs:
  - Add views, serializers, models in app modules.
  - Register app routes in `app_name/urls.py`, then include from `server/server/urls.py`.
- Keep error response behavior consistent with `custom_exception_handler`.
- Reuse `StandardResultsSetPagination` for list endpoints needing pagination consistency.
- When changing auth/registration flows, verify compatibility with:
  - `dj-rest-auth` routes
  - allauth templates
  - `AccountAdapter` URL rewriting.
- If adding role-based behavior, follow custom user boolean flags pattern (`is_admin`, `is_customer`) unless migrating to a dedicated role model.

## 6) Security/config notes for contributors

Current settings are development-friendly and should be hardened in production:

- Move secrets (`SECRET_KEY`, SMTP creds) to environment variables.
- Set `DEBUG = False` in production.
- Restrict `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and CORS origins.
- Revisit `JWT_AUTH_HTTPONLY` cookie strategy based on frontend architecture.

## 7) Quick start workflow

From repository root:

```bash
cd server
python manage.py makemigrations
python manage.py migrate
python manage.py runserver
```

Optional maintenance script:

```bash
bash remove_migration_files.sh
```

Use migration cleanup carefully and only when resetting local development state.