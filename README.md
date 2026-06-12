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

## Publicar no Render

1. Crie uma conta no Render.
2. Crie um repositorio no GitHub com estes arquivos.
3. No Render, escolha **New +** e depois **Blueprint** se quiser usar o `render.yaml`.
4. Conecte o repositorio do GitHub.
5. O blueprint cria:
   - um Web Service Python;
   - um PostgreSQL chamado `ps-plus-tracker-db`;
   - a variavel `DATABASE_URL` ligada ao banco.
6. Configure as variaveis que ficaram como `sync: false`:
   - `ADMIN_USER`
   - `ADMIN_PASSWORD`
7. Aguarde o deploy terminar e abra a URL publica do Render.
8. Faca login e importe o arquivo TXT pela tela do app.

## Alternativa mais barata

Se quiser evitar o PostgreSQL pago do Render, crie o banco gratuito no Supabase e use no Render apenas o Web Service.

Nesse caso, no Render:

- nao use a secao `databases` do `render.yaml`;
- crie a variavel `DATABASE_URL` manualmente com a connection string do Supabase;
- mantenha `SECRET_KEY`, `ADMIN_USER` e `ADMIN_PASSWORD`.

## Variaveis de ambiente

```text
DATABASE_URL=postgresql://...
SECRET_KEY=uma-chave-grande-aleatoria
ADMIN_USER=seu-usuario
ADMIN_PASSWORD=sua-senha
```

Sem `DATABASE_URL`, o app usa SQLite em `data/ps_plus.db`.
