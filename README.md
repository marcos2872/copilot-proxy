# Copilot Proxy

Proxy OpenAI-compatible que usa o GitHub Copilot como backend. Permite usar ferramentas como OpenWebUI, Continue, ou qualquer cliente OpenAI com sua assinatura do Copilot.

## Como funciona

```
OpenWebUI / Cliente  →  Copilot Proxy (localhost:8484)  →  GitHub Copilot API
     (OpenAI format)       /v1/chat/completions              (roteamento por modelo)
```

O proxy:
1. Autentica via OAuth Device Flow (igual ao VS Code)
2. Expõe endpoints compatíveis com a API OpenAI
3. Roteia para o backend correto baseado no modelo:
   - GPT-4o, Gemini, Grok → `/chat/completions` (pass-through)
   - GPT-5.x → `/responses` (tradução de formato)
   - Claude 4.x → `/v1/messages` (tradução Anthropic → OpenAI)
4. Converte as respostas de volta para formato OpenAI Chat Completions

## Instalação

### Docker Compose (recomendado)

Sobe o proxy + OpenWebUI juntos:

```bash
docker compose up -d
```

Na primeira vez, autentique:

```bash
curl -X POST http://localhost:8484/auth/login
```

Abra o link retornado no browser e entre o codigo. Pronto — acesse `http://localhost:3000` e os modelos do Copilot ja estarao disponiveis.

### Local (sem Docker)

```bash
uv sync
uv run copilot-proxy --login
```

Na primeira execução, será exibido um código de autenticação:
```
==================================================
  GitHub Copilot Proxy — Authentication
==================================================
  1. Open:  https://github.com/login/device
  2. Enter: WDJB-MJHT
==================================================
```

Abra o link no browser e entre o código. Após autorizar, o servidor inicia.

## Uso

### Iniciar o servidor (com credenciais salvas)

```bash
uv run copilot-proxy
```

O token é salvo em `~/.config/copilot-proxy/token.json`.

### Opções

```bash
uv run copilot-proxy --host 0.0.0.0 --port 8484
```

## Configuração no OpenWebUI

### Via Docker Compose

Ja vem pré-configurado — o `docker-compose.yml` define `OPENAI_API_BASE_URL` apontando para o proxy automaticamente.

### Manual

1. Vá em **Settings → Connections → OpenAI API**
2. Configure:
   - **URL:** `http://localhost:8484/v1`
   - **API Key:** `sk-dummy` (qualquer valor — o proxy gerencia a auth)
3. Salve. Os modelos do Copilot aparecerão na lista.

## Endpoints

| Método | Path | Descrição |
|--------|------|-----------|
| GET | `/v1/models` | Lista modelos disponíveis |
| POST | `/v1/chat/completions` | Chat completions (streaming/non-streaming) |
| GET | `/auth/status` | Status da autenticação |
| POST | `/auth/login` | Inicia device flow (retorna código + URI) |
| GET | `/health` | Health check |

## Modelos disponíveis

Os modelos são listados dinamicamente da API do Copilot (`GET /models`).
Qualquer modelo novo liberado pelo GitHub aparece automaticamente.

Roteamento:
- `gpt-5*` → endpoint `/responses`
- Todos os outros (GPT-4o, Claude, Gemini, Grok) → endpoint `/chat/completions`

## Funcionalidades

- Listagem dinamica de modelos (novos modelos aparecem automaticamente)
- Streaming SSE (Server-Sent Events)
- Tool calls / Function calling
- Suporte a respostas non-streaming
- Docker Compose com OpenWebUI integrado

## Login via API (sem terminal)

Se o servidor já estiver rodando sem autenticação:

```bash
curl -X POST http://localhost:8484/auth/login
```

Resposta:
```json
{
  "verification_uri": "https://github.com/login/device",
  "user_code": "WDJB-MJHT",
  "expires_in": 900,
  "message": "Open https://github.com/login/device and enter code: WDJB-MJHT"
}
```

Após completar no browser, a autenticação é estabelecida automaticamente.

## Requisitos

- Docker + Docker Compose (para uso com containers)
- Ou: Python 3.11+ e [uv](https://docs.astral.sh/uv/) (para uso local)
- Assinatura ativa do GitHub Copilot (Individual ou Business)
