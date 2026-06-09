# Copilot Proxy

Proxy OpenAI-compatible que usa o GitHub Copilot como backend. Permite usar ferramentas como OpenWebUI, Continue, ou qualquer cliente OpenAI com sua assinatura do Copilot.

## Como funciona

```
OpenWebUI / Cliente  →  Copilot Proxy (localhost:7677)  →  GitHub Copilot API
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
curl -X POST http://localhost:7677/auth/login
```

Abra o link retornado no browser e entre o codigo. Pronto — acesse `http://localhost:7676` e os modelos do Copilot ja estarao disponiveis.

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
uv run copilot-proxy --host 0.0.0.0 --port 7677
```

## Configuração no OpenWebUI

### Via Docker Compose

Ja vem pré-configurado — o `docker-compose.yml` define `OPENAI_API_BASE_URL` apontando para o proxy automaticamente.

### Manual

1. Vá em **Settings → Connections → OpenAI API**
2. Configure:
   - **URL:** `http://localhost:7677/v1`
   - **API Key:** `sk-dummy` (qualquer valor — o proxy gerencia a auth)
3. Salve. Os modelos do Copilot aparecerão na lista.

## Ferramentas (MCP) no OpenWebUI

O OpenWebUI consome ferramentas via **OpenAPI**, não MCP direto. Para usar MCP servers
(como `chrome-devtools`, `filesystem`, `fetch`), use o
[`mcpo`](https://github.com/open-webui/mcpo) — um proxy que expõe cada MCP como API OpenAPI.

### 1. Configurar os MCPs

Crie um `config-mcp.json`. **Atenção ao runtime de cada server**: alguns são Node
(`npx`), outros são Python (`uvx`). Usar o comando errado faz o server subir **sem
nenhuma ferramenta** (OpenAPI vazio) e o modelo dirá que não tem a tool.

```json
{
  "mcpServers": {
    "chrome-devtools": {
      "command": "npx",
      "args": ["-y", "chrome-devtools-mcp@latest"]
    },
    "filesystem": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/home/marcos/Projects/copilot-proxy"
      ]
    },
    "sequential-thinking": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"]
    },
    "fetch": {
      "command": "uvx",
      "args": ["mcp-server-fetch"]
    },
    "time": {
      "command": "uvx",
      "args": ["mcp-server-time"]
    }
  }
}
```

| MCP | Runtime | Pacote |
|-----|---------|--------|
| chrome-devtools | `npx` | `chrome-devtools-mcp` |
| filesystem | `npx` | `@modelcontextprotocol/server-filesystem` |
| sequential-thinking | `npx` | `@modelcontextprotocol/server-sequential-thinking` |
| fetch | `uvx` | `mcp-server-fetch` (Python) |
| time | `uvx` | `mcp-server-time` (Python) |

### 2. Subir o mcpo (na máquina, fora do container)

```bash
uvx mcpo --config config-mcp.json --port 7678
```

Mantenha o `mcpo` rodando enquanto usar as ferramentas — ele segura os processos dos MCPs.
O `chrome-devtools` abre um Chrome real na sua máquina quando o modelo usa a tool.

Confira que cada MCP expõe ferramentas (OpenAPI com `paths` não-vazio):

```bash
curl -s http://localhost:7678/fetch/openapi.json | python3 -c "import sys,json; print(list(json.load(sys.stdin)['paths']))"
```

### 3. Registrar no OpenWebUI

Cada MCP tem seu próprio **subpath** OpenAPI (`/chrome-devtools`, `/filesystem`, etc.) —
nunca use `http://localhost:7678` sozinho. Há **duas formas** de registrar, e a URL muda
conforme **quem busca o spec**:

**Opção A — Manual (Settings → Tools).** A validação é feita pelo **navegador** (host),
então use `localhost`:

| Nome | URL |
|------|-----|
| Chrome DevTools | `http://localhost:7678/chrome-devtools` |
| Filesystem | `http://localhost:7678/filesystem` |
| Fetch | `http://localhost:7678/fetch` |

**Opção B — Pré-configurado no `docker-compose.yml`.** Aqui quem busca o spec é o
**backend** (container), que **não enxerga `localhost`** da máquina — use
`host.docker.internal` (requer `extra_hosts: host.docker.internal:host-gateway` no
serviço, já presente no compose):

```yaml
    environment:
      - ENABLE_PERSISTENT_CONFIG=false
      - >-
        TOOL_SERVER_CONNECTIONS=[
        {"url":"http://host.docker.internal:7678/fetch","path":"openapi.json","auth_type":"bearer","key":"","config":{"enable":true}},
        {"url":"http://host.docker.internal:7678/filesystem","path":"openapi.json","auth_type":"bearer","key":"","config":{"enable":true}}
        ]
```

> **localhost vs host.docker.internal:** Tool Servers manuais (por usuário) são buscados
> pelo **navegador** → `localhost`. Os globais via `TOOL_SERVER_CONNECTIONS` são buscados
> pelo **backend** → `host.docker.internal`. Trocar errado causa
> `Failed to connect to ... OpenAPI tool server` nos logs.

### 4. Ativar no chat

- No seletor de **Ferramentas** da conversa, ligue os MCPs desejados.
- Defina o **Function Calling** do modelo como **`Native`**
  (Controls → Advanced Params, ou Workspace → Models). Tool Servers externos exigem
  `Native`; no modo `Default` o modelo costuma ignorar as ferramentas.
- O modelo precisa suportar **function calling**.


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
curl -X POST http://localhost:7677/auth/login
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
