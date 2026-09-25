import requests, json

url = "http://127.0.0.1:8000/api/criar-assinatura/"
headers = {
    "Content-Type": "application/json",
    "X-Central-Token": "1OrMpZe_H7S0aydMvVhynpJvJP7ssYD2"
}
dados = {
    "email": "teste2@teste.com",
    "sistema": "catalogo",
    "plano": "anual",
    "nome": "Jose Teste 2"
}

r = requests.post(url, headers=headers, json=dados)
print(r.status_code)
print(r.text)