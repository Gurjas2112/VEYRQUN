import os

APP_NAME = "VEYRQUN"
APP_TITLE = "VEYRQUN | Governance Layer for Financial Agents"

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Read on every call (not at import) so tests can point each case at a fresh database.
def db_path() -> str:
    return os.getenv("VEYRQUN_DB_FILE", os.path.join(_BACKEND_DIR, "veyrqun.db"))


def simulator_enabled() -> bool:
    return os.getenv("VEYRQUN_SIMULATOR", "1").lower() not in ("0", "false", "no")


def allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "*")
    if raw.strip() == "*":
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]
