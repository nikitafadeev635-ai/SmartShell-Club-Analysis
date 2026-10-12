import requests
import json

# Ссылка на API
API_URL = "https://billing.smartshell.gg/api/graphql"

# Запрос интроспекции (спрашивает у сервера: "Что ты умеешь?")
INTROSPECTION_QUERY = """
{
  __schema {
    types {
      name
      fields {
        name
        type { name kind ofType { name kind } }
      }
    }
  }
}
"""

try:
    # Сначала получаем токен (если API закрыт авторизацией)
    # Если API публичный, можно убрать этот блок и не слать токен
    login_response = requests.post(API_URL, json={
        "query": """
        mutation login {
            login(input: {login: "79147018226", password: "95241243", company_id: 1598}) {
                access_token
            }
        }
        """
    })
    token = login_response.json()["data"]["login"]["access_token"]
    
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Делаем запрос схемы
    response = requests.post(API_URL, json={"query": INTROSPECTION_QUERY}, headers=headers)
    data = response.json()
    
    # Сохраняем в файл для удобного чтения
    with open("smartshell_schema.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        
    print("✅ Схема успешно сохранена в smartshell_schema.json")
    print("🔍 Открой этот файл и поищи 'Shift' или 'WorkShift'")

except Exception as e:
    print(f"❌ Ошибка: {e}")