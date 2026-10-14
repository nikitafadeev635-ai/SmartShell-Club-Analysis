# === SMARTSHELL API (для проверки гостей на точках) ===
SMARTSHELL_GRAPHQL_URL = os.getenv("SMARTSHELL_GRAPHQL_URL", "https://billing.smartshell.gg/api/graphql")
SMARTSHELL_LOGIN = os.getenv("SMARTSHELL_LOGIN")
SMARTSHELL_PASSWORD = os.getenv("SMARTSHELL_PASSWORD")
SMARTSHELL_COMPANY_ID = int(os.getenv("SMARTSHELL_COMPANY_ID", "1598"))