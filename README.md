# PS Plus Tracker

Aplicativo web em Flask para controlar jogos da PS Plus com historico de entradas e saidas.

## Recursos

- Login simples por usuario e senha.
- PostgreSQL em producao via `DATABASE_URL`.
- SQLite local como fallback para desenvolvimento.
- Importacao do TXT inicial.
- Historico por jogo, permitindo casos como:
  - entrou no Extra;
  - saiu do catalogo;
  - voltou depois no Essential.

## Arquivos principais

- `app.py`: aplicacao Flask.
- `requirements.txt`: dependencias instaladas pelo Render.
- `Procfile`: comando de start.
- `render.yaml`: blueprint opcional para criar app e banco no Render.

## Rodar localmente

Instale Python 3.11+ e depois:

```powershell
cd C:\Users\marlon.bertan\Documents\app-psplus
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
$env:SECRET_KEY='dev-secret'
$env:ADMIN_USER='admin'
$env:ADMIN_PASSWORD='uma-senha-forte'
python app.py
```

Acesse:

```text
http://127.0.0.1:8000
```

## Importar o TXT local

Com o ambiente virtual ativo:

```powershell
python app.py --import 'C:\marlon\ps plus.txt'
```

Tambem da para importar pela propria tela do app depois do login.

## Publicar gratuitamente com Render + Neon

1. Crie uma conta gratuita em https://neon.com.
2. No Neon, crie um projeto PostgreSQL.
3. Copie a connection string do banco. Use a conexao pooled quando ela estiver disponivel.
4. Crie uma conta no Render.
5. No Render, escolha **New +** e depois **Blueprint**.
6. Conecte o repositorio do GitHub.
7. Configure as variaveis que ficaram como `sync: false`:
   - `ADMIN_USER`
   - `ADMIN_PASSWORD`
   - `DATABASE_URL`: cole a connection string do Neon.
8. O `SECRET_KEY` sera gerado automaticamente pelo Render.
9. Aguarde o deploy terminar e abra a URL publica do Render.
10. Faca login e importe o arquivo TXT pela tela do app.

O plano Free do Neon possui limites de armazenamento e uso, mas nao expira depois de 30 dias. Para este aplicativo pessoal, os limites gratuitos tendem a ser suficientes.

## Variaveis de ambiente

```text
DATABASE_URL=postgresql://...
SECRET_KEY=uma-chave-grande-aleatoria
ADMIN_USER=seu-usuario
ADMIN_PASSWORD=sua-senha
```

Sem `DATABASE_URL`, o app usa SQLite em `data/ps_plus.db`.
