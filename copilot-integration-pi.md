# GitHub Copilot API — Integration Guide

Este guia documenta o protocolo completo de integração com a API do GitHub Copilot, obtido por análise do código-fonte. Cobre autenticação OAuth via Device Flow, troca de token Copilot, descoberta de modelos, chamada de chat (OpenAI Completions, OpenAI Responses e Anthropic Messages), uso de tools, thinking/reasoning e suporte a Enterprise.

---

## Índice

1. [Visão Geral](#visão-geral)
2. [Client ID](#client-id)
3. [Autenticação — OAuth Device Flow](#autenticação--oauth-device-flow)
4. [Troca pelo Token Copilot](#troca-pelo-token-copilot)
5. [Extração da Base URL a partir do Token](#extração-da-base-url-a-partir-do-token)
6. [GitHub Enterprise](#github-enterprise)
7. [Headers Obrigatórios](#headers-obrigatórios)
8. [Listagem e Ativação de Modelos](#listagem-e-ativação-de-modelos)
9. [Modelos Disponíveis](#modelos-disponíveis)
10. [Modo Chat — OpenAI Chat Completions (SSE)](#modo-chat--openai-chat-completions-sse)
11. [Modo Chat — OpenAI Responses API](#modo-chat--openai-responses-api)
12. [Modo Chat — Anthropic Messages (Claude 4.x)](#modo-chat--anthropic-messages-claude-4x)
13. [Tool Calls (Function Calling)](#tool-calls-function-calling)
14. [Thinking / Reasoning](#thinking--reasoning)
15. [Visão (Imagens)](#visão-imagens)
16. [Renovação de Token](#renovação-de-token)
17. [Exemplos Completos por Linguagem](#exemplos-completos-por-linguagem)

---

## Visão Geral

O GitHub Copilot expõe três APIs de chat distintas dependendo do modelo escolhido:

| API | Quando usar | Endpoint path |
|-----|------------|---------------|
| OpenAI Chat Completions | GPT-4o, GPT-4.1, Gemini, Grok | `POST /chat/completions` |
| OpenAI Responses | GPT-5.x | `POST /responses` |
| Anthropic Messages | Claude 4.x (Haiku, Sonnet, Opus) | `POST /v1/messages` |

Todos compartilham a mesma base URL e o mesmo mecanismo de autenticação (Bearer token Copilot).

---

## Client ID

O Client ID usado no Device Flow é o da extensão oficial do VS Code:

```
Iv1.b507a08c87ecfe98
```

> **Nota:** Este Client ID é o que identifica sua aplicação perante o GitHub OAuth. Ele pertence à extensão GitHub Copilot Chat para VS Code (`vscode-chat`). Para projetos em produção, considere registrar seu próprio OAuth App no GitHub.

---

## Autenticação — OAuth Device Flow

O GitHub Copilot usa o [RFC 8628 — OAuth 2.0 Device Authorization Grant](https://datatracker.ietf.org/doc/html/rfc8628).

### Passo 1 — Solicitar Device Code

```http
POST https://github.com/login/device/code
Content-Type: application/x-www-form-urlencoded
Accept: application/json
User-Agent: GitHubCopilotChat/0.35.0

client_id=Iv1.b507a08c87ecfe98&scope=read%3Auser
```

**Resposta:**

```json
{
  "device_code": "3584d83530557fdd1f46af8289938c8ef79f9dc5",
  "user_code": "WDJB-MJHT",
  "verification_uri": "https://github.com/login/device",
  "interval": 5,
  "expires_in": 900
}
```

| Campo | Descrição |
|-------|-----------|
| `device_code` | Código interno para polling |
| `user_code` | Código de 8 dígitos exibido ao usuário |
| `verification_uri` | URL onde o usuário digita o código |
| `interval` | Intervalo mínimo entre polls (segundos) |
| `expires_in` | Validade do código (segundos) |

**O que fazer:** Exibir `verification_uri` e `user_code` ao usuário. Ele deve abrir o browser, acessar a URL e digitar o código.

---

### Passo 2 — Polling para Obter o Access Token GitHub

Após aguardar `interval * 1.2` segundos (margem de segurança), faça polling:

```http
POST https://github.com/login/oauth/access_token
Content-Type: application/x-www-form-urlencoded
Accept: application/json
User-Agent: GitHubCopilotChat/0.35.0

client_id=Iv1.b507a08c87ecfe98&device_code=<device_code>&grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Adevice_code
```

**Respostas possíveis:**

```json
// Ainda aguardando o usuário:
{ "error": "authorization_pending", "error_description": "..." }

// Polou rápido demais:
{ "error": "slow_down", "error_description": "...", "interval": 10 }

// Sucesso:
{ "access_token": "ghu_XXXXXXXXXXXXXXXXXX", "token_type": "bearer", "scope": "read:user" }
```

**Lógica de retry:**
- `authorization_pending` → aguardar `interval * 1.2` e repetir
- `slow_down` → usar o novo `interval` do servidor multiplicado por `1.4`
- Qualquer outro erro → falha definitiva
- Respeitar `expires_in` como deadline máximo

O token resultante (`ghu_...`) é um **GitHub Personal Access Token** com escopo `read:user`. Ele serve apenas para trocar pelo token Copilot — nunca é enviado diretamente à API de chat.

---

## Troca pelo Token Copilot

O token GitHub (`ghu_...`) deve ser trocado por um **token de sessão Copilot** de curta duração (~30 min):

```http
GET https://api.github.com/copilot_internal/v2/token
Authorization: Bearer ghu_XXXXXXXXXXXXXXXXXX
Accept: application/json
User-Agent: GitHubCopilotChat/0.35.0
Editor-Version: vscode/1.107.0
Editor-Plugin-Version: copilot-chat/0.35.0
Copilot-Integration-Id: vscode-chat
```

**Resposta:**

```json
{
  "token": "tid=abc123;exp=1700000000;proxy-ep=proxy.individual.githubcopilot.com;sku=copilot_for_individuals;...",
  "expires_at": 1700000000
}
```

| Campo | Descrição |
|-------|-----------|
| `token` | Token de sessão Copilot (formato semicolon-separated key=value) |
| `expires_at` | Unix timestamp de expiração (segundos) |

> **Importante:** O token Copilot expira. Armazene o token GitHub (`ghu_...`) como refresh token e troque novamente quando o token Copilot expirar. A implementação de referência subtrai 5 minutos do `expires_at` como margem de segurança: `expires = expires_at * 1000 - 5 * 60 * 1000`.

---

## Extração da Base URL a partir do Token

O token Copilot contém o campo `proxy-ep` que determina qual endpoint usar:

```
tid=abc;exp=1700000000;proxy-ep=proxy.individual.githubcopilot.com;sku=copilot_for_individuals
```

**Conversão:**

```
proxy.individual.githubcopilot.com  →  api.individual.githubcopilot.com
proxy.business.githubcopilot.com    →  api.business.githubcopilot.com
```

Regra: substituir o prefixo `proxy.` por `api.`.

```python
import re

def get_base_url(token: str) -> str:
    match = re.search(r'proxy-ep=([^;]+)', token)
    if not match:
        return "https://api.individual.githubcopilot.com"
    proxy_host = match.group(1)
    api_host = proxy_host.replace("proxy.", "api.", 1)
    return f"https://{api_host}"
```

**Fallback:** Se não encontrar `proxy-ep`, usar `https://api.individual.githubcopilot.com`.

---

## GitHub Enterprise

Para GitHub Enterprise (GHE), as URLs mudam:

| Endpoint | URL |
|----------|-----|
| Device code | `https://<domain>/login/device/code` |
| Access token | `https://<domain>/login/oauth/access_token` |
| Token Copilot | `https://api.<domain>/copilot_internal/v2/token` |
| API base | `https://copilot-api.<domain>` |

Exemplo para `company.ghe.com`:
- Device code: `https://company.ghe.com/login/device/code`
- Token Copilot: `https://api.company.ghe.com/copilot_internal/v2/token`
- API base: `https://copilot-api.company.ghe.com`

---

## Headers Obrigatórios

### Headers Estáticos (todas as requisições)

Estes headers devem estar presentes em **todas** as chamadas para a API Copilot, incluindo troca de token e chat:

```http
User-Agent: GitHubCopilotChat/0.35.0
Editor-Version: vscode/1.107.0
Editor-Plugin-Version: copilot-chat/0.35.0
Copilot-Integration-Id: vscode-chat
```

### Headers de Autenticação (chat)

```http
Authorization: Bearer <copilot_session_token>
```

O token Copilot (`tid=...`) vai diretamente no header `Authorization: Bearer`. Não é um JWT — é o token bruto retornado pelo endpoint `/copilot_internal/v2/token`.

### Headers Dinâmicos (por requisição de chat)

Devem ser calculados por requisição:

```http
X-Initiator: user | agent
Openai-Intent: conversation-edits
```

**`X-Initiator`:**
- `user` → a última mensagem do histórico é do papel `user`
- `agent` → a última mensagem é `assistant` ou `toolResult` (resposta após tool use)

```python
def get_x_initiator(messages: list) -> str:
    if not messages:
        return "user"
    last = messages[-1]
    return "user" if last["role"] == "user" else "agent"
```

**`Copilot-Vision-Request: true`** — adicionar somente quando o payload contiver imagens (no conteúdo de mensagens `user` ou `toolResult`).

### Ativação de Modelos (policy)

Para ativar modelos como Claude e Grok que requerem aceitação de política:

```http
Content-Type: application/json
Authorization: Bearer <copilot_session_token>
User-Agent: GitHubCopilotChat/0.35.0
Editor-Version: vscode/1.107.0
Editor-Plugin-Version: copilot-chat/0.35.0
Copilot-Integration-Id: vscode-chat
openai-intent: chat-policy
x-interaction-type: chat-policy
```

---

## Listagem e Ativação de Modelos

### Listar modelos disponíveis

Não há um endpoint de listagem documentado. Os modelos são descobertos por referência externa (ex.: [models.dev](https://models.dev/api.json)) e configurados estaticamente.

### Ativar um modelo (aceitar política)

Alguns modelos (Claude, Grok) precisam ser ativados antes do primeiro uso:

```http
POST https://api.individual.githubcopilot.com/models/<model_id>/policy
Authorization: Bearer <copilot_session_token>
Content-Type: application/json
openai-intent: chat-policy
x-interaction-type: chat-policy
[+ headers estáticos]

{"state": "enabled"}
```

Exemplo:
```
POST https://api.individual.githubcopilot.com/models/claude-sonnet-4/policy
```

**Resposta esperada:** `200 OK` (corpo ignorado).

Recomenda-se chamar este endpoint para todos os modelos logo após o login.

---

## Modelos Disponíveis

### Base URL padrão

```
https://api.individual.githubcopilot.com
```

### Modelos via OpenAI Chat Completions

| Model ID | Nome | Thinking | Visão | Context | Max Output |
|----------|------|----------|-------|---------|------------|
| `gpt-4o` | GPT-4o | ❌ | ✅ | 128k | 4k |
| `gpt-4.1` | GPT-4.1 | ❌ | ✅ | 128k | 16k |
| `gemini-2.5-pro` | Gemini 2.5 Pro | ❌ | ✅ | 128k | 64k |
| `gemini-3-flash-preview` | Gemini 3 Flash | ✅ | ✅ | 128k | 64k |
| `gemini-3-pro-preview` | Gemini 3 Pro | ✅ | ✅ | 128k | 64k |
| `gemini-3.1-pro-preview` | Gemini 3.1 Pro | ✅ | ✅ | 128k | 64k |
| `grok-code-fast-1` | Grok Code Fast 1 | ✅ | ❌ | 128k | 64k |

**Compat flags para modelos Copilot via Completions:**
- `supportsStore: false` — não enviar o campo `store`
- `supportsDeveloperRole: false` — usar `system` em vez de `developer`
- `supportsReasoningEffort: false` — não enviar `reasoning_effort`
- `stream_options: { include_usage: true }` — suportado para obter tokens no streaming

### Modelos via OpenAI Responses API

| Model ID | Nome | Thinking | Visão | Context | Max Output |
|----------|------|----------|-------|---------|------------|
| `gpt-5` | GPT-5 | ✅ | ✅ | 128k | 128k |
| `gpt-5-mini` | GPT-5-mini | ✅ | ✅ | 264k | 64k |
| `gpt-5.1` | GPT-5.1 | ✅ | ✅ | 264k | 64k |
| `gpt-5.1-codex` | GPT-5.1 Codex | ✅ | ✅ | 400k | 128k |
| `gpt-5.1-codex-max` | GPT-5.1 Codex Max | ✅ | ✅ | 400k | 128k |
| `gpt-5.1-codex-mini` | GPT-5.1 Codex Mini | ✅ | ✅ | 400k | 128k |
| `gpt-5.2` | GPT-5.2 | ✅ | ✅ | 264k | 64k |
| `gpt-5.2-codex` | GPT-5.2 Codex | ✅ | ✅ | 400k | 128k |
| `gpt-5.3-codex` | GPT-5.3 Codex | ✅ | ✅ | 400k | 128k |
| `gpt-5.4` | GPT-5.4 | ✅ | ✅ | 400k | 128k |
| `gpt-5.4-mini` | GPT-5.4 mini | ✅ | ✅ | 400k | 128k |

> Para GPT-5.x: **não** enviar `reasoning: { effort: "none" }` quando thinking não for desejado — simplesmente omitir o campo `reasoning`.

### Modelos via Anthropic Messages API

| Model ID | Nome | Thinking | Visão | Context | Max Output |
|----------|------|----------|-------|---------|------------|
| `claude-sonnet-4` | Claude Sonnet 4 | ✅ | ✅ | 216k | 16k |
| `claude-sonnet-4.5` | Claude Sonnet 4.5 | ✅ | ✅ | 144k | 32k |
| `claude-sonnet-4.6` | Claude Sonnet 4.6 | ✅ | ✅ | 1M | 32k |
| `claude-opus-4.5` | Claude Opus 4.5 | ✅ | ✅ | 160k | 32k |
| `claude-opus-4.6` | Claude Opus 4.6 | ✅ | ✅ | 1M | 64k |
| `claude-haiku-4.5` | Claude Haiku 4.5 | ✅ | ✅ | 144k | 32k |

---

## Modo Chat — OpenAI Chat Completions (SSE)

**Endpoint:**
```
POST https://api.individual.githubcopilot.com/chat/completions
```

**Headers completos:**
```http
Authorization: Bearer <copilot_token>
Content-Type: application/json
User-Agent: GitHubCopilotChat/0.35.0
Editor-Version: vscode/1.107.0
Editor-Plugin-Version: copilot-chat/0.35.0
Copilot-Integration-Id: vscode-chat
X-Initiator: user
Openai-Intent: conversation-edits
```

**Payload:**
```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello, who are you?"}
  ],
  "stream": true,
  "stream_options": {"include_usage": true}
}
```

> **Atenção:** Não enviar `store: false` nem usar o papel `developer` — esses campos não são suportados pelo Copilot.

**Resposta (SSE):**
```
data: {"id":"chatcmpl-xxx","choices":[{"delta":{"content":"I am"},"finish_reason":null}]}
data: {"id":"chatcmpl-xxx","choices":[{"delta":{"content":" an AI"},"finish_reason":null}]}
data: {"id":"chatcmpl-xxx","choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":15,"completion_tokens":8}}
data: [DONE]
```

---

## Modo Chat — OpenAI Responses API

**Endpoint:**
```
POST https://api.individual.githubcopilot.com/responses
```

**Headers:** idênticos ao Chat Completions.

**Payload:**
```json
{
  "model": "gpt-5-mini",
  "input": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": [{"type": "input_text", "text": "Hello!"}]}
  ],
  "stream": true,
  "store": false
}
```

> **Atenção sobre thinking:** Para GPT-5.x no Copilot, **não** enviar `reasoning: { effort: "none" }`. Omitir completamente o campo `reasoning` quando não quiser thinking. Enviar `reasoning: { effort: "medium" }` apenas quando quiser ativar.

**Resposta (SSE):** protocolo padrão OpenAI Responses com eventos `response.output_text.delta`, etc.

---

## Modo Chat — Anthropic Messages (Claude 4.x)

Modelos Claude 4.x no Copilot usam o protocolo Anthropic Messages, mas com diferenças importantes em relação à API direta da Anthropic.

**Endpoint:**
```
POST https://api.individual.githubcopilot.com/v1/messages
```

**Headers obrigatórios:**
```http
Authorization: Bearer <copilot_token>
Content-Type: application/json
User-Agent: GitHubCopilotChat/0.35.0
Editor-Version: vscode/1.107.0
Editor-Plugin-Version: copilot-chat/0.35.0
Copilot-Integration-Id: vscode-chat
X-Initiator: user
Openai-Intent: conversation-edits
accept: application/json
anthropic-dangerous-direct-browser-access: true
```

> **Diferença crítica:** Usar `Authorization: Bearer <token>` em vez de `x-api-key`. O SDK Anthropic suporta isso via `authToken` (com `apiKey: null`).

**Betas suportados:** somente `interleaved-thinking-2025-05-14` (quando thinking estiver ativo). **Não** incluir `fine-grained-tool-streaming-2025-05-14` — não suportado.

**Payload:**
```json
{
  "model": "claude-sonnet-4",
  "messages": [
    {"role": "user", "content": "Hello!"}
  ],
  "system": [
    {"type": "text", "text": "You are a helpful assistant."}
  ],
  "max_tokens": 8192,
  "stream": true
}
```

**Resposta (SSE):** protocolo padrão Anthropic com eventos `content_block_start`, `content_block_delta`, `message_delta`, etc.

---

## Tool Calls (Function Calling)

### Via OpenAI Chat Completions

```json
{
  "model": "gpt-4o",
  "messages": [...],
  "stream": true,
  "stream_options": {"include_usage": true},
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {"type": "string", "description": "City name"}
          },
          "required": ["city"]
        },
        "strict": false
      }
    }
  ]
}
```

> Enviar `"strict": false` — alguns providers rejeitam o campo, mas o Copilot aceita.

**Resposta com tool call:**
```json
{
  "choices": [{
    "delta": {
      "tool_calls": [{
        "index": 0,
        "id": "call_abc123",
        "type": "function",
        "function": {
          "name": "get_weather",
          "arguments": "{\"city\": \"London\"}"
        }
      }]
    },
    "finish_reason": "tool_calls"
  }]
}
```

**Enviando o resultado de volta:**
```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "user", "content": "What's the weather in London?"},
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "call_abc123",
        "type": "function",
        "function": {"name": "get_weather", "arguments": "{\"city\": \"London\"}"}
      }]
    },
    {
      "role": "tool",
      "content": "Cloudy, 15°C",
      "tool_call_id": "call_abc123"
    }
  ],
  "stream": true,
  "tools": [...]
}
```

> **Atenção:** O Copilot requer que o array `tools` seja enviado novamente mesmo em turnos de follow-up que contêm histórico de tool use.

### Via Anthropic Messages (Claude 4.x)

```json
{
  "model": "claude-sonnet-4",
  "messages": [
    {"role": "user", "content": "What's the weather in London?"}
  ],
  "tools": [
    {
      "name": "get_weather",
      "description": "Get current weather for a city",
      "input_schema": {
        "type": "object",
        "properties": {
          "city": {"type": "string"}
        },
        "required": ["city"]
      }
    }
  ],
  "max_tokens": 8192,
  "stream": true
}
```

**Enviando resultado de tool:**
```json
{
  "messages": [
    {"role": "user", "content": "What's the weather in London?"},
    {
      "role": "assistant",
      "content": [
        {
          "type": "tool_use",
          "id": "toolu_01abc",
          "name": "get_weather",
          "input": {"city": "London"}
        }
      ]
    },
    {
      "role": "user",
      "content": [
        {
          "type": "tool_result",
          "tool_use_id": "toolu_01abc",
          "content": "Cloudy, 15°C"
        }
      ]
    }
  ]
}
```

---

## Thinking / Reasoning

### Modelos GPT-5.x via Responses API

Ativar thinking:
```json
{
  "model": "gpt-5-mini",
  "reasoning": {
    "effort": "medium",
    "summary": "auto"
  },
  "include": ["reasoning.encrypted_content"]
}
```

**Desativar:** omitir o campo `reasoning` completamente (não enviar `{ "effort": "none" }`).

Níveis de `effort`: `"minimal"`, `"low"`, `"medium"`, `"high"`, `"xhigh"`.

### Modelos Claude via Anthropic Messages

**Modelos Claude Sonnet 4 e Opus 4.5 (budget-based):**
```json
{
  "model": "claude-sonnet-4",
  "thinking": {
    "type": "enabled",
    "budget_tokens": 8000
  },
  "max_tokens": 16000
}
```

**Modelos Claude Sonnet 4.6 e Opus 4.6 (adaptive — não precisam de budget):**
```json
{
  "model": "claude-sonnet-4.6",
  "thinking": {
    "type": "adaptive"
  },
  "output_config": {
    "effort": "high"
  },
  "max_tokens": 32000
}
```

Valores de `effort` para adaptive thinking: `"low"`, `"medium"`, `"high"`, `"max"` (Opus 4.6 only).

**Desativar thinking:**
```json
{
  "thinking": {"type": "disabled"}
}
```

**Beta necessário para interleaved thinking** (modelos não-adaptive):
```http
anthropic-beta: interleaved-thinking-2025-05-14
```

**Não incluir** `fine-grained-tool-streaming-2025-05-14` — não suportado pelo Copilot.

### Modelos OpenAI Completions (GPT-4o, Gemini, etc.)

Esses modelos não suportam reasoning via Copilot — não enviar `reasoning_effort`.

---

## Visão (Imagens)

### Adicionar imagem em mensagem user (Completions/Responses)

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "What is in this image?"},
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/jpeg;base64,<base64_data>"
      }
    }
  ]
}
```

Adicionar header extra:
```http
Copilot-Vision-Request: true
```

### Adicionar imagem em mensagem user (Anthropic Messages)

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "What is in this image?"},
    {
      "type": "image",
      "source": {
        "type": "base64",
        "media_type": "image/jpeg",
        "data": "<base64_data>"
      }
    }
  ]
}
```

Adicionar o mesmo header:
```http
Copilot-Vision-Request: true
```

Modelos sem visão (ex.: `grok-code-fast-1`): filtrar imagens do payload antes de enviar.

---

## Renovação de Token

O token Copilot tem validade de aproximadamente 30 minutos. Para renovar, basta chamar novamente o endpoint de troca com o token GitHub original:

```http
GET https://api.github.com/copilot_internal/v2/token
Authorization: Bearer ghu_XXXXXXXXXXXXXXXXXX
[+ headers estáticos]
```

**Estratégia recomendada:**
1. Armazenar `ghu_...` (refresh token) com segurança
2. Calcular `expires = expires_at * 1000 - 5 * 60 * 1000` (5 min antes do vencimento)
3. Antes de cada requisição, verificar se `Date.now() >= expires`
4. Se expirado, chamar o endpoint de renovação
5. Após renovação, atualizar a base URL a partir do novo token (o campo `proxy-ep` pode mudar)

---

## Exemplos Completos por Linguagem

### Python

```python
import re
import time
import base64
import json
import requests

# ─── Constantes ──────────────────────────────────────────────────────────────

CLIENT_ID = "Iv1.b507a08c87ecfe98"

STATIC_HEADERS = {
    "User-Agent": "GitHubCopilotChat/0.35.0",
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
}

# ─── Autenticação ─────────────────────────────────────────────────────────────

def start_device_flow() -> dict:
    resp = requests.post(
        "https://github.com/login/device/code",
        headers={**STATIC_HEADERS, "Accept": "application/json"},
        data={"client_id": CLIENT_ID, "scope": "read:user"},
    )
    resp.raise_for_status()
    return resp.json()


def poll_for_github_token(device_code: str, interval: int, expires_in: int) -> str:
    deadline = time.time() + expires_in
    poll_interval = interval * 1.2

    while time.time() < deadline:
        time.sleep(poll_interval)
        resp = requests.post(
            "https://github.com/login/oauth/access_token",
            headers={**STATIC_HEADERS, "Accept": "application/json"},
            data={
                "client_id": CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        if "access_token" in data:
            return data["access_token"]
        elif data.get("error") == "slow_down":
            poll_interval = (data.get("interval", interval) + 5) * 1.4
        elif data.get("error") != "authorization_pending":
            raise RuntimeError(f"OAuth error: {data.get('error')}: {data.get('error_description')}")

    raise TimeoutError("Device flow timed out")


def get_copilot_token(github_token: str) -> dict:
    """Troca o token GitHub pelo token de sessão Copilot."""
    resp = requests.get(
        "https://api.github.com/copilot_internal/v2/token",
        headers={**STATIC_HEADERS, "Authorization": f"Bearer {github_token}", "Accept": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()


def get_base_url(copilot_token: str) -> str:
    match = re.search(r'proxy-ep=([^;]+)', copilot_token)
    if not match:
        return "https://api.individual.githubcopilot.com"
    return f"https://{match.group(1).replace('proxy.', 'api.', 1)}"


def login() -> dict:
    """Fluxo completo de login. Retorna credentials dict."""
    device = start_device_flow()
    print(f"Open: {device['verification_uri']}")
    print(f"Code: {device['user_code']}")

    github_token = poll_for_github_token(
        device["device_code"], device["interval"], device["expires_in"]
    )
    copilot_data = get_copilot_token(github_token)
    base_url = get_base_url(copilot_data["token"])

    return {
        "github_token": github_token,       # refresh token
        "copilot_token": copilot_data["token"],
        "expires": copilot_data["expires_at"] * 1000 - 5 * 60 * 1000,  # ms
        "base_url": base_url,
    }


def ensure_valid_token(creds: dict) -> dict:
    """Renova o token Copilot se necessário."""
    if time.time() * 1000 >= creds["expires"]:
        data = get_copilot_token(creds["github_token"])
        creds["copilot_token"] = data["token"]
        creds["expires"] = data["expires_at"] * 1000 - 5 * 60 * 1000
        creds["base_url"] = get_base_url(data["token"])
    return creds

# ─── Chat ─────────────────────────────────────────────────────────────────────

def get_dynamic_headers(messages: list, has_images: bool = False) -> dict:
    last = messages[-1] if messages else {}
    initiator = "user" if last.get("role") == "user" else "agent"
    headers = {
        "X-Initiator": initiator,
        "Openai-Intent": "conversation-edits",
    }
    if has_images:
        headers["Copilot-Vision-Request"] = "true"
    return headers


def chat_stream(creds: dict, model: str, messages: list, system: str = None, tools: list = None):
    """Streaming chat via OpenAI Chat Completions."""
    creds = ensure_valid_token(creds)
    base_url = creds["base_url"]

    payload = {
        "model": model,
        "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [],
    }

    if system:
        payload["messages"].append({"role": "system", "content": system})
    payload["messages"].extend(messages)

    if tools:
        payload["tools"] = tools

    headers = {
        **STATIC_HEADERS,
        "Authorization": f"Bearer {creds['copilot_token']}",
        "Content-Type": "application/json",
        **get_dynamic_headers(messages),
    }

    with requests.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json=payload,
        stream=True,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if line.startswith(b"data: "):
                data = line[6:]
                if data == b"[DONE]":
                    break
                chunk = json.loads(data)
                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                if delta.get("content"):
                    print(delta["content"], end="", flush=True)


def chat_with_thinking(creds: dict, model: str, messages: list, system: str = None):
    """Chat com thinking ativado para Claude Sonnet 4 (budget-based)."""
    creds = ensure_valid_token(creds)
    base_url = creds["base_url"]

    anthropic_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
    ]

    payload = {
        "model": model,
        "messages": anthropic_messages,
        "max_tokens": 16000,
        "stream": True,
        "thinking": {
            "type": "enabled",
            "budget_tokens": 8000,
        },
    }

    if system:
        payload["system"] = [{"type": "text", "text": system}]

    headers = {
        **STATIC_HEADERS,
        "Authorization": f"Bearer {creds['copilot_token']}",
        "Content-Type": "application/json",
        "accept": "application/json",
        "anthropic-dangerous-direct-browser-access": "true",
        "anthropic-beta": "interleaved-thinking-2025-05-14",
        **get_dynamic_headers(messages),
    }

    with requests.post(
        f"{base_url}/v1/messages",
        headers=headers,
        json=payload,
        stream=True,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if line.startswith(b"data: "):
                event = json.loads(line[6:])
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        print(delta["text"], end="", flush=True)
                    elif delta.get("type") == "thinking_delta":
                        print(f"[thinking: {delta['thinking']}]", end="", flush=True)


def enable_model(creds: dict, model_id: str) -> bool:
    """Ativa um modelo (ex: Claude, Grok) aceitando a política."""
    creds = ensure_valid_token(creds)
    try:
        resp = requests.post(
            f"{creds['base_url']}/models/{model_id}/policy",
            headers={
                **STATIC_HEADERS,
                "Authorization": f"Bearer {creds['copilot_token']}",
                "Content-Type": "application/json",
                "openai-intent": "chat-policy",
                "x-interaction-type": "chat-policy",
            },
            json={"state": "enabled"},
        )
        return resp.ok
    except Exception:
        return False


# ─── Uso ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    creds = login()

    # Ativar modelos Claude e Grok
    for model_id in ["claude-sonnet-4", "claude-sonnet-4.5", "grok-code-fast-1"]:
        ok = enable_model(creds, model_id)
        print(f"  {model_id}: {'ok' if ok else 'failed'}")

    # Chat simples com GPT-4o
    chat_stream(creds, "gpt-4o", [{"role": "user", "content": "Hello!"}])

    # Chat com thinking (Claude Sonnet 4)
    chat_with_thinking(
        creds,
        "claude-sonnet-4",
        [{"role": "user", "content": "Solve: integral of x^2"}],
        system="You are a math expert.",
    )
```

---

### JavaScript / TypeScript (Node.js)

```typescript
import * as https from "https";
import * as http from "http";

const CLIENT_ID = "Iv1.b507a08c87ecfe98";

const STATIC_HEADERS = {
  "User-Agent": "GitHubCopilotChat/0.35.0",
  "Editor-Version": "vscode/1.107.0",
  "Editor-Plugin-Version": "copilot-chat/0.35.0",
  "Copilot-Integration-Id": "vscode-chat",
};

// ─── Tipos ────────────────────────────────────────────────────────────────────

interface Credentials {
  githubToken: string;
  copilotToken: string;
  expires: number;    // ms
  baseUrl: string;
}

interface Message {
  role: "user" | "assistant" | "system" | "tool";
  content: string | object[];
  tool_calls?: object[];
  tool_call_id?: string;
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────

function fetchJson(url: string, options: { method?: string; headers?: Record<string, string>; body?: string }): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const req = https.request(
      { hostname: urlObj.hostname, path: urlObj.pathname + urlObj.search, method: options.method || "GET", headers: options.headers },
      (res) => {
        let data = "";
        res.on("data", (chunk) => (data += chunk));
        res.on("end", () => {
          try {
            resolve(JSON.parse(data));
          } catch {
            resolve(data);
          }
        });
      }
    );
    req.on("error", reject);
    if (options.body) req.write(options.body);
    req.end();
  });
}

// ─── Device Flow ──────────────────────────────────────────────────────────────

async function startDeviceFlow(): Promise<{ device_code: string; user_code: string; verification_uri: string; interval: number; expires_in: number }> {
  const params = new URLSearchParams({ client_id: CLIENT_ID, scope: "read:user" });
  return fetchJson("https://github.com/login/device/code", {
    method: "POST",
    headers: { ...STATIC_HEADERS, Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    body: params.toString(),
  }) as any;
}

async function pollForGitHubToken(deviceCode: string, intervalSec: number, expiresIn: number): Promise<string> {
  const deadline = Date.now() + expiresIn * 1000;
  let pollMs = intervalSec * 1200;

  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, pollMs));

    const params = new URLSearchParams({
      client_id: CLIENT_ID,
      device_code: deviceCode,
      grant_type: "urn:ietf:params:oauth:grant-type:device_code",
    });

    const data: any = await fetchJson("https://github.com/login/oauth/access_token", {
      method: "POST",
      headers: { ...STATIC_HEADERS, Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
      body: params.toString(),
    });

    if (data.access_token) return data.access_token;
    if (data.error === "slow_down") pollMs = ((data.interval ?? intervalSec) + 5) * 1400;
    else if (data.error !== "authorization_pending") throw new Error(`OAuth error: ${data.error}`);
  }

  throw new Error("Device flow timed out");
}

function getBaseUrl(copilotToken: string): string {
  const match = copilotToken.match(/proxy-ep=([^;]+)/);
  if (!match) return "https://api.individual.githubcopilot.com";
  return `https://${match[1].replace("proxy.", "api.")}`;
}

async function getCopilotToken(githubToken: string): Promise<{ token: string; expires_at: number }> {
  return fetchJson("https://api.github.com/copilot_internal/v2/token", {
    headers: { ...STATIC_HEADERS, Authorization: `Bearer ${githubToken}`, Accept: "application/json" },
  }) as any;
}

export async function login(): Promise<Credentials> {
  const device = await startDeviceFlow();
  console.log(`Open: ${device.verification_uri}`);
  console.log(`Code: ${device.user_code}`);

  const githubToken = await pollForGitHubToken(device.device_code, device.interval, device.expires_in);
  const data = await getCopilotToken(githubToken);

  return {
    githubToken,
    copilotToken: data.token,
    expires: data.expires_at * 1000 - 5 * 60 * 1000,
    baseUrl: getBaseUrl(data.token),
  };
}

async function ensureValidToken(creds: Credentials): Promise<Credentials> {
  if (Date.now() >= creds.expires) {
    const data = await getCopilotToken(creds.githubToken);
    creds.copilotToken = data.token;
    creds.expires = data.expires_at * 1000 - 5 * 60 * 1000;
    creds.baseUrl = getBaseUrl(data.token);
  }
  return creds;
}

// ─── Chat Streaming ───────────────────────────────────────────────────────────

function getDynamicHeaders(messages: Message[], hasImages = false): Record<string, string> {
  const last = messages[messages.length - 1];
  const initiator = last?.role === "user" ? "user" : "agent";
  return {
    "X-Initiator": initiator,
    "Openai-Intent": "conversation-edits",
    ...(hasImages ? { "Copilot-Vision-Request": "true" } : {}),
  };
}

export async function* chatStream(
  creds: Credentials,
  model: string,
  messages: Message[],
  options: { system?: string; tools?: object[]; thinking?: boolean } = {}
): AsyncGenerator<string> {
  creds = await ensureValidToken(creds);

  const payload: Record<string, unknown> = {
    model,
    stream: true,
    stream_options: { include_usage: true },
    messages: options.system
      ? [{ role: "system", content: options.system }, ...messages]
      : messages,
  };

  if (options.tools) payload.tools = options.tools;

  const headers = {
    ...STATIC_HEADERS,
    Authorization: `Bearer ${creds.copilotToken}`,
    "Content-Type": "application/json",
    ...getDynamicHeaders(messages),
  };

  for await (const chunk of streamRequest(`${creds.baseUrl}/chat/completions`, headers, payload)) {
    const choice = chunk.choices?.[0];
    if (choice?.delta?.content) yield choice.delta.content;
    if (choice?.delta?.tool_calls) {
      // handle tool calls
      yield JSON.stringify({ toolCalls: choice.delta.tool_calls });
    }
  }
}

export async function* chatAnthropicStream(
  creds: Credentials,
  model: string,
  messages: Message[],
  options: { system?: string; thinking?: boolean; tools?: object[] } = {}
): AsyncGenerator<{ type: "text" | "thinking"; content: string }> {
  creds = await ensureValidToken(creds);

  const payload: Record<string, unknown> = {
    model,
    messages,
    max_tokens: 16000,
    stream: true,
  };

  if (options.system) {
    payload.system = [{ type: "text", text: options.system }];
  }

  if (options.thinking) {
    payload.thinking = { type: "enabled", budget_tokens: 8000 };
  }

  if (options.tools) payload.tools = options.tools;

  const betas: string[] = [];
  if (options.thinking) betas.push("interleaved-thinking-2025-05-14");

  const headers: Record<string, string> = {
    ...STATIC_HEADERS,
    Authorization: `Bearer ${creds.copilotToken}`,
    "Content-Type": "application/json",
    accept: "application/json",
    "anthropic-dangerous-direct-browser-access": "true",
    ...getDynamicHeaders(messages),
  };

  if (betas.length > 0) headers["anthropic-beta"] = betas.join(",");

  for await (const event of streamRequest(`${creds.baseUrl}/v1/messages`, headers, payload)) {
    if (event.type === "content_block_delta") {
      const delta = event.delta;
      if (delta?.type === "text_delta") yield { type: "text", content: delta.text };
      if (delta?.type === "thinking_delta") yield { type: "thinking", content: delta.thinking };
    }
  }
}

// ─── SSE streaming helper ─────────────────────────────────────────────────────

async function* streamRequest(
  url: string,
  headers: Record<string, string>,
  body: unknown
): AsyncGenerator<Record<string, unknown>> {
  const urlObj = new URL(url);
  const bodyStr = JSON.stringify(body);

  const response = await new Promise<http.IncomingMessage>((resolve, reject) => {
    const req = https.request(
      {
        hostname: urlObj.hostname,
        path: urlObj.pathname,
        method: "POST",
        headers: { ...headers, "Content-Length": Buffer.byteLength(bodyStr) },
      },
      resolve
    );
    req.on("error", reject);
    req.write(bodyStr);
    req.end();
  });

  let buffer = "";
  for await (const chunk of response) {
    buffer += chunk.toString();
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        const data = line.slice(6).trim();
        if (data === "[DONE]") return;
        try {
          yield JSON.parse(data);
        } catch {
          // skip malformed
        }
      }
    }
  }
}

// ─── Model Policy ─────────────────────────────────────────────────────────────

export async function enableModel(creds: Credentials, modelId: string): Promise<boolean> {
  creds = await ensureValidToken(creds);
  try {
    const data: any = await fetchJson(`${creds.baseUrl}/models/${modelId}/policy`, {
      method: "POST",
      headers: {
        ...STATIC_HEADERS,
        Authorization: `Bearer ${creds.copilotToken}`,
        "Content-Type": "application/json",
        "openai-intent": "chat-policy",
        "x-interaction-type": "chat-policy",
      },
      body: JSON.stringify({ state: "enabled" }),
    });
    return true;
  } catch {
    return false;
  }
}
```

---

## Referência Rápida — Tabela de Endpoints

| Operação | Método | URL |
|----------|--------|-----|
| Solicitar device code | `POST` | `https://github.com/login/device/code` |
| Trocar device code por token | `POST` | `https://github.com/login/oauth/access_token` |
| Trocar token GitHub por token Copilot | `GET` | `https://api.github.com/copilot_internal/v2/token` |
| Ativar modelo (policy) | `POST` | `https://api.individual.githubcopilot.com/models/{model_id}/policy` |
| Chat (OpenAI Completions) | `POST` | `https://api.individual.githubcopilot.com/chat/completions` |
| Chat (OpenAI Responses) | `POST` | `https://api.individual.githubcopilot.com/responses` |
| Chat (Anthropic Claude 4.x) | `POST` | `https://api.individual.githubcopilot.com/v1/messages` |

> A base URL `https://api.individual.githubcopilot.com` é o fallback. O valor correto deve ser extraído do campo `proxy-ep` do token Copilot conforme descrito na seção [Extração da Base URL](#extração-da-base-url-a-partir-do-token).

---

## Notas Importantes

1. **Não usar `store: false`** com modelos Copilot via Completions — o campo não é suportado.
2. **Não usar o papel `developer`** — usar `system`.
3. **Não enviar `reasoning_effort`** para modelos via Completions no Copilot.
4. **Não enviar `reasoning: { effort: "none" }`** para GPT-5.x — omitir o campo `reasoning` completamente.
5. **Não incluir `fine-grained-tool-streaming-2025-05-14`** no header `anthropic-beta` para Claude no Copilot.
6. **Bearer auth para Claude:** usar `Authorization: Bearer <token>` (não `x-api-key`).
7. **Tool history:** ao re-enviar histórico que contém tool calls/results, incluir o array `tools` novamente no payload.
8. **Normalização de tool call IDs:** IDs podem conter `|` como separador — extrair apenas a parte antes do `|` e sanitizar para `[a-zA-Z0-9_-]`, máximo 40 caracteres.
9. **Thinking incompatível com temperature:** ao ativar thinking em modelos Anthropic, omitir o campo `temperature`.
