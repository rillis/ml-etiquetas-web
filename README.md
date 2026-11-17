# ml-etiquetas-web

Painel web da expedição das vendas do Mercado Livre. Reúne num só lugar as vendas que ainda precisam de nota fiscal ou de etiqueta e transforma a rotina de "emitir NF → imprimir etiquetas → avisar o fornecedor" em um clique.

## Funcionalidades

**Lista de vendas.** Mostra as vendas do Mercado Envios que estão aguardando a NF-e (`invoice_pending`) ou que já têm etiqueta liberada e ainda não foram impressas (`ready_to_print`), com comprador, itens (SKU e quantidade), valor, previsão de entrega e marcação de B2B.

**Processamento.** As vendas selecionadas são processadas em segundo plano, um lote por vez:

1. emite uma NF-e por venda pelo Faturador do Mercado Livre (`POST /users/{id}/invoices/orders`), usando os dados fiscais já cadastrados nos anúncios;
2. acompanha a autorização na SEFAZ e a liberação das etiquetas, mostrando o andamento de cada venda;
3. se passar do tempo configurado sem liberar, avisa no Discord e continua esperando;
4. gera os arquivos do lote:
   - `AAAAMMDD-HHMM_Danfe.pdf`: os DANFEs, em A4, um por página;
   - `<nome do ML>_labels.pdf`: as etiquetas 10x15 cm com o resumo de separação do Mercado Livre no fim;
5. monta a mensagem de vendas para o fornecedor ("Bom dia! Tudo bem? Segue vendas de hoje: …"), com os tratamentos de SKU, kits e quantidades por extenso, e cruza os itens com o estoque de devoluções.

O estado do lote fica salvo em disco; se o container reiniciar, o processamento continua de onde parou.

**Histórico.** Os lotes dos últimos 30 dias ficam disponíveis com os PDFs e a mensagem.

**Alerta no Discord.** Em dias úteis (sem fins de semana e feriados nacionais) e dentro de uma janela de horário, verifica periodicamente quantas etiquetas estão pendentes. Quando passa do limite, menciona o cargo configurado com a contagem por tipo e o link do painel. Para não repetir à toa, só volta a mencionar se entrar uma venda nova na fila ou depois do intervalo de lembrete, e zera quando a fila volta ao limite.

## Arquitetura

- Python 3.11, Flask + gunicorn (site e API) e um worker (processamentos, alerta e limpeza do histórico), no mesmo container.
- Front-end em HTML, CSS e JavaScript puros, servidos pelo próprio Flask.
- O token do Mercado Livre é o do [ml-jrf](https://github.com/rillis/ml-jrf), lido da pasta `data` dele (montada só leitura). Num 401, o serviço pede ao ml-jrf para conferir o token (`/token/ping`) e relê o arquivo; ele nunca renova o token sozinho.
- O estoque de devoluções é consultado no MySQL, só leitura.
- Estado e arquivos gerados ficam em `data/` (`estado/` e `output/<AAAAMMDD-HHMM>/`).

```
app/
├── web.py            site e API
├── worker.py         laço do worker
├── processamento.py  lote: NF-e, espera, PDFs e mensagem
├── alerta.py         regra do alerta no Discord
├── vendas.py         vendas aguardando NF ou prontas
├── pdfs.py           DANFEs e etiquetas
├── mensagem.py       mensagem do fornecedor e estoque de devoluções
├── ml.py             cliente da API do Mercado Livre
├── discord.py        webhook do Discord
├── estado.py         arquivos de estado
├── config.py         configuração (.env)
├── templates/ e static/
```

## Configuração

Variáveis no `.env` (modelo em `.env.example`):

| Variável | Descrição |
|---|---|
| `MLJRF_DATA_DIR` | Pasta `data` do ml-jrf (caminho absoluto), onde está o `tokens.json` |
| `ML_JRF_PING_URL` | Endpoint `/token/ping` do ml-jrf |
| `DISCORD_WEBHOOK_URL` | Webhook do canal das mensagens |
| `DISCORD_ROLE_ID` | Cargo mencionado nos alertas |
| `SITE_URL` | Link do painel nas mensagens |
| `ALERTA_ATIVO` | `1` liga o alerta periódico |
| `ALERTA_INICIO` / `ALERTA_FIM` | Janela do alerta (`HH:MM`) |
| `ALERTA_INTERVALO_MIN` | Minutos entre verificações |
| `ALERTA_LIMITE` | Menciona quando houver mais pendentes que isso |
| `ALERTA_LEMBRETE_MIN` | Minutos até lembrar de novo com a fila parada |
| `AVISO_DEMORA_MIN` | Minutos de processamento até avisar no Discord |
| `INTERVALO_CONSULTA` | Segundos entre consultas durante o processamento |
| `DIAS_BUSCA` | Dias de pedidos considerados na lista |
| `HISTORICO_DIAS` | Dias de histórico guardados |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASS`, `DB_NAME`, `DB_SSL_CA` | MySQL do estoque de devoluções |

## Execução

```bash
docker compose up -d --build
```

O painel responde na porta 51015. `GET /health` informa se o worker está ativo.
