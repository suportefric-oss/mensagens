# Detalhes de uso e manutenção

## Credenciais do Telegram

O painel usa a API de usuário por meio do Telethon. Você precisa de `api_id` e `api_hash` do seu próprio aplicativo. O portal oficial de desenvolvimento do Telegram é https://my.telegram.org → **API development tools**. O portal pode pedir login e dados do aplicativo, e a disponibilidade é controlada pelo Telegram. Não use credenciais de terceiros. O código recebido no Telegram serve para conectar sua conta dentro do painel que você hospedou; não é uma variável de ambiente.

## O que entra na lista

- Grupos comuns e supergrupos públicos ou privados onde a conta pode enviar texto.
- Canais públicos ou privados onde a conta é dona ou tem permissão de publicar.
- Tópicos como Geral, Boas-vindas e outros: são enumerados com paginação e tratados como destinos separados, respeitando permissões do grupo e o estado do tópico.
- A varredura começa automaticamente ao conectar ou restaurar a sessão. Lista principal e arquivados são consultados explicitamente, sem cadastrar IDs ou links. Ao terminar sem avisos, todos os destinos encontrados ficam selecionados; você pode desmarcar qualquer um. Nenhuma mensagem é enviada sem a confirmação final.
- Conversas individuais, bots no privado e Mensagens Salvas são excluídos no servidor, inclusive na etapa de envio.

O painel não entra em grupos nem analisa o conteúdo das conversas. Ele consulta diálogos, permissões, metadados de tópicos e informações necessárias para obter links existentes. Tópicos ocultos são excluídos; fechados só entram para dono ou administrador com permissão de gerenciar tópicos. A consulta de permissões não garante entrega: o Telegram decide a permissão final, inclusive para restrições específicas. Permissões e estado dos tópicos são verificados novamente antes de cada envio, que é direcionado ao tópico correspondente. Os envios são reais, pela conta conectada. Publique de acordo com as regras dos grupos e canais.

## Exclusão solicitada

`zenitrylogs` é excluído por nome, nome de usuário, aliases, nome de tópico e links. A comparação ignora maiúsculas, acentos e separadores. A exclusão vale para lista principal, arquivados, links exportados e envio; também remove registros identificáveis dessa origem do último envio salvo ao reiniciar. O servidor verifica o destino novamente antes de publicar. Uma origem com nome e identificação totalmente diferentes não pode ser reconhecida apenas por esse texto.

## Links e andamento da varredura

O painel exibe o progresso e informa quando a consulta está incompleta, por exemplo, por falha de rede ou pausa do Telegram. Não apresenta uma varredura parcial como completa. Se houver avisos, confira a lista antes de selecionar e enviar. É possível parar a varredura e atualizar novamente depois. Restaurar uma sessão pode reutilizar uma varredura concluída nos últimos cinco minutos para evitar consultas repetidas.

Links públicos usam o nome de usuário do grupo/canal. Para grupos privados, usa somente convites existentes retornados pelo Telegram, quando disponíveis à sua conta; nenhum convite é criado. Tópicos privados têm links internos que funcionam para membros. Se não houver um link disponível, o destino continua listado com essa indicação. Copiar/baixar links inclui todos os destinos com links disponíveis, independentemente da seleção. Convites privados são sensíveis: compartilhe apenas com quem deve entrar.

## Controles

- Texto simples, com até 4.096 unidades UTF-16 (emojis podem contar como duas). Sem anexos, agendamento ou repetição automática. Links são enviados sem prévia.
- **Selecionar todos** inclui toda a lista encontrada, mesmo com filtro de busca ativo. A revisão mostra todos os destinos selecionados.
- Uma publicação por vez. Uma nova publicação substitui o histórico anterior do painel; mensagens antigas continuam no Telegram.
- O intervalo padrão é 10 segundos, mínimo 5. Nenhum intervalo garante que o Telegram permitirá os envios.
- **Parar envio** impede os próximos envios. Uma solicitação já em andamento ainda pode concluir. Não desfaz mensagens já enviadas.
- Se o Telegram pedir espera (FloodWait/SlowMode), a fila pausa. Depois do prazo, você pode clicar em **Continuar pendentes**. Restrições de conta encerram a fila, sem contorno de bloqueios.
- Se houver queda de conexão ou reinicialização, destinos com resultado incerto não são repetidos. Confira diretamente no Telegram. Apenas destinos pendentes podem continuar, mediante comando seu.
- Fechar a aba ou sair do painel não para um envio iniciado. Use o botão **Parar envio** para isso. Desconectar a conta encerra a sessão Telegram usada pelo aplicativo.

## Dados e acesso

Use uma única instância do servidor. A pasta `data/` guarda a sessão Telegram e o último envio com mensagem, destinos e resultados; faça backup antes de atualizar. A persistência depende do armazenamento da hospedagem. Opcionalmente, `DATA_DIR` aponta para uma pasta persistente. Nunca publique ou compartilhe essa pasta: a sessão permite operar sua conta. O painel não oferece links públicos.

Códigos e senhas Telegram são usados somente para autenticar e não são gravados nos logs ou arquivos do aplicativo. A sessão autenticada permanece no servidor. Sessões web do dono duram oito horas e expiram quando o servidor reinicia. **Desconectar conta do Telegram** revoga a sessão do aplicativo; também é possível encerrá-la em Telegram → Configurações → Dispositivos.

## Rodar no computador

1. Crie um ambiente virtual: `python -m venv .venv`.
2. Instale: `.venv/bin/pip install -r requirements.txt` (no Windows, use `.venv\Scripts\pip`).
3. Copie `.env.example` para `.env`, preencha as credenciais e use `COOKIE_SECURE=false` e `PORT=8080` para HTTP local.
4. Execute `.venv/bin/python app.py` e abra http://localhost:8080.

O `.env` local é carregado automaticamente; variáveis já definidas pela hospedagem têm prioridade. Em produção, use HTTPS e `COOKIE_SECURE=true`.

## Testes

`python -m unittest discover -s tests -v`

Os testes usam uma conta Telegram simulada. Nenhuma conta real foi conectada e nenhuma mensagem real foi enviada durante o desenvolvimento. A conexão real depende das suas credenciais, do Telegram e da rede da hospedagem.
