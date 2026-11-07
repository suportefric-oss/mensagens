# Relay — Painel Telegram

## Colocar no SquareCloud

1. Abra `.env.example` e troque `OWNER_PASSWORD` por uma senha sua de 12+ caracteres.
2. Preencha `TELEGRAM_API_ID` e `TELEGRAM_API_HASH` do seu aplicativo Telegram. São credenciais de aplicativo, não o token de um bot. Não envie essas informações no chat.
3. Importe ou copie as seis linhas nas variáveis de ambiente da hospedagem. Deixe `COOKIE_SECURE=true` para usar pelo domínio HTTPS.
4. Ajuste `SUBDOMAIN` em `squarecloud.app` para um nome disponível. Envie os arquivos do ZIP na raiz da aplicação. A hospedagem deve instalar `requirements.txt` e executar `python app.py` com Python 3.10+.
5. Abra o domínio HTTPS e entre com a senha do painel. Em **Conexão da conta**, informe seu telefone com DDI, o código enviado pelo Telegram e, quando solicitada, sua senha de duas etapas.
6. Clique em **Atualizar lista**, depois em **Selecionar todos** (ou escolha individualmente). Cole o texto e clique em **Revisar envio**. Confira os destinos e confirme.

O domínio web e a disponibilidade contínua dependem do seu plano. A implantação no SquareCloud não foi feita nesta sessão. Se a plataforma fornecer uma porta específica, use esse valor em `PORT`.

Mais informações sobre credenciais, limites, backup e testes em `DETALHES.md`.
