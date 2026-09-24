import requests, json

url = "http://127.0.0.1:8000/api/criar-assinatura/"
headers = {
    "Content-Type": "application/json",
    "X-Central-Token": "innosoft_teste_123"
}
# Você disse que tem anual e semestral, então usa anual
dados = {
    "email": "teste@teste.com",
    "sistema": "catalogo",
    "plano": "anual", # muda pra semestral se quiser testar
    "nome": "Jose Teste"
}

r = requests.post(url, headers=headers, json=dados)
print(r.status_code)
print(r.text)