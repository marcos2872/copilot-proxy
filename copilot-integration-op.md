# Integração GitHub Copilot — Referência Técnica Completa

> Documento de referência para replicar a integração com o GitHub Copilot Chat em qualquer linguagem ou aplicação.
> Reflete o fluxo real implementado e validado neste projeto.

---

## Visão Geral

O fluxo usa um único token permanente — sem troca intermediária:

```
OAuth Token (permanente, salvo em disco)
    └─> Chamadas de Chat / Modelos (Bearer direto)
```

> **Não há troca adicional de token.** O `access_token` obtido no Device Flow
> (`ghu_...`) é usado diretamente como `Bearer` em todas as chamadas à API do Copilot.

---

## Parte 1 — Autenticação (GitHub Device Flow)

Implementa o [GitHub Device Flow (OAuth 2.0)](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow).
Executado **uma única vez** — o `oauth_token` resultante é persistido em disco.

### Configuração

CLIENT_ID = "Iv1.b507a08c87ecfe98" (copilot VSCode)
CLIENT_ID = "Ov23li8tweQw6odWQebz" (opencode/VSCode)
| Parâmetro | Valor |
| :-------------------------- | :------------------------------------------------- |
| `client_id` | `Ov23li8tweQw6odWQebz` (Client ID público opencode/VSCode) |
| `scope` | `read:user` |
| `User-Agent` | qualquer string (ex: `meu-app/1.0`) |
| Timeout de polling | 600 s (10 minutos) |
| Intervalo mínimo de polling | `max(interval_da_api, 5)` segundos |

---

### Passo 1.1 — Solicitar código do dispositivo

**Request:**

```
POST https://github.com/login/device/code
```

Headers:

```
Accept:       application/json
Content-Type: application/json
User-Agent:   meu-app/1.0
```

Body (JSON):

```json
{
  "client_id": "Ov23li8tweQw6odWQebz",
  "scope": "read:user"
}
```

**Response de sucesso (200 OK):**

```json
{
  "device_code": "3584d83530557fdd1f46af8289938c8ef79f9dc5",
  "user_code": "WDJB-MJHT",
  "verification_uri": "https://github.com/login/device",
  "expires_in": 900,
  "interval": 5
}
```

**Ação após sucesso:** exibir `user_code` e `verification_uri` ao usuário e iniciar polling.

---

### Passo 1.2 — Polling do access token

Repetir a request abaixo em loop até obter o token ou atingir timeout (10 min).

**Request:**

```
POST https://github.com/login/oauth/access_token
```

Headers:

```
Accept:       application/json
Content-Type: application/json
User-Agent:   meu-app/1.0
```

Body (JSON):

```json
{
  "client_id": "Ov23li8tweQw6odWQebz",
  "device_code": "<device_code_recebido_no_passo_1.1>",
  "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
}
```

**Response de sucesso (200 OK — token disponível):**

```json
{
  "access_token": "ghu_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "token_type": "bearer",
  "scope": "read:user"
}
```

**Máquina de estados de polling:**

| Campo `data.error`                | Ação                                                  |
| :-------------------------------- | :---------------------------------------------------- |
| `authorization_pending`           | Aguarda `interval + 3` segundos e tenta novamente     |
| `slow_down`                       | Incrementa `interval += 5` segundos e tenta novamente |
| Qualquer outro valor              | Lança exceção fatal com `error_description`           |
| Ausente + `access_token` presente | **Sucesso** — salvar e usar o token                   |
| Ausente + sem token               | Formato inesperado — continuar polling                |

> **Nota:** `authorization_pending` soma **+3 s** ao intervalo base (além do próprio `interval`).
> `slow_down` soma **+5 s** ao intervalo corrente.

**Erro de rede:** suprimir erros de rede transitórios e continuar o loop.

**Persistência:** salvar `access_token` (o `oauth_token`) em disco. Este token é
**suficiente para todas as chamadas subsequentes** — não é necessário nenhuma troca adicional.

---

## Parte 2 — Listar Modelos Disponíveis

Usa o `oauth_token` **diretamente** como Bearer — sem troca de token intermediária.

### Headers obrigatórios em todas as chamadas à API Copilot

```
Authorization:    Bearer <oauth_token>
User-Agent:       meu-app/1.0
Openai-Intent:    conversation-edits
x-initiator:      user
Content-Type:     application/json
```

> **Não envie** `x-api-key`, `Editor-Version`, `Editor-Plugin-Version` nem
> `Copilot-Integration-Id` — são desnecessários e podem causar conflito.

---

### Passo 2.1 — Listar modelos disponíveis

**Request:**

```
GET https://api.githubcopilot.com/models
```

**Response (dois formatos possíveis):**

Formato array:

```json
[
  {
    "id": "gpt-4o",
    "name": "GPT-4o",
    "model_picker_enabled": true,
    "capabilities": { "type": "chat" },
    "policy": { "state": "enabled" }
  }
]
```

Formato objeto com `data`:

```json
{
  "data": [{ "...": "..." }]
}
```

**Filtro de modelos válidos (todos os três devem ser verdadeiros):**

```
model_picker_enabled == true
capabilities.type    == "chat"
policy.state         == "enabled"
```

---

## Parte 3 — Chat com o Copilot

A API do Copilot expõe **dois endpoints** de chat. O endpoint correto depende do modelo:

| Modelos                             | Endpoint                 |
| :---------------------------------- | :----------------------- |
| GPT-4o, Claude (Anthropic) e Gemini | `POST /chat/completions` |
| GPT-5+                              | `POST /responses`        |

> A detecção é feita pelo prefixo do nome do modelo: se começa com `"gpt-5"`, usa `/responses`.

---

### Passo 3.1 — Chat Completions (GPT-4o e anteriores)

**Request:**

```
POST https://api.githubcopilot.com/chat/completions
```

Headers:

```
Authorization:    Bearer <oauth_token>
User-Agent:       meu-app/1.0
Openai-Intent:    conversation-edits
x-initiator:      user
Content-Type:     application/json
```

**Body (JSON) — sem ferramentas:**

```json
{
  "messages": [
    { "role": "system", "content": "Você é um assistente útil." },
    { "role": "user", "content": "Olá, como vai?" }
  ],
  "model": "gpt-4o",
  "stream": true
}
```

**Body (JSON) — com ferramentas (formato OpenAI):**

```json
{
  "messages": ["..."],
  "model": "gpt-4o",
  "stream": true,
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "listar_templates",
        "description": "Lista os templates disponíveis",
        "parameters": {
          "type": "object",
          "properties": {},
          "required": []
        }
      }
    }
  ],
  "tool_choice": "auto"
}
```

---

#### Parsing do Stream SSE — /chat/completions

A API retorna `text/event-stream`. Cada linha tem o prefixo `data: ` seguido de JSON:

```
data: {"choices":[{"delta":{"role":"assistant","content":"Olá"},"finish_reason":null}]}
data: {"choices":[{"delta":{"content":"!"}}]}
data: {"choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]
```

**Algoritmo:**

```
Para cada linha do stream:
  1. Ignorar linhas sem prefixo "data: "
  2. Se payload == "[DONE]" → encerrar
  3. JSON.parse → choices[0].delta
  4. Acumular delta.content (texto)
  5. Para tool_calls: acumular arguments por índice (chegam em fragmentos)
```

**Resultado de tool_call:**

```json
{
  "role": "assistant",
  "content": null,
  "tool_calls": [
    {
      "id": "call_abc123",
      "type": "function",
      "function": {
        "name": "listar_templates",
        "arguments": "{}"
      }
    }
  ]
}
```

---

### Passo 3.2 — Responses API (GPT-5+)

Modelos com prefixo `gpt-5` não suportam `/chat/completions`. Usar `/responses`.

**Request:**

```
POST https://api.githubcopilot.com/responses
```

Headers idênticos ao `/chat/completions`:

```
Authorization:    Bearer <oauth_token>
User-Agent:       meu-app/1.0
Openai-Intent:    conversation-edits
x-initiator:      user
Content-Type:     application/json
```

**Body (JSON) — sem ferramentas:**

```json
{
  "model": "gpt-5.4",
  "input": [
    { "role": "system", "content": [{ "type": "input_text", "text": "Você é um assistente." }] },
    { "role": "user", "content": [{ "type": "input_text", "text": "Olá!" }] }
  ],
  "stream": true
}
```

**Body (JSON) — com ferramentas:**

> O formato de tools na Responses API é **diferente** do `/chat/completions`:
> não há o wrapper `"function"` — os campos ficam diretamente no objeto.

```json
{
  "model": "gpt-5.4",
  "input": ["..."],
  "stream": true,
  "tools": [
    {
      "type": "function",
      "name": "listar_templates",
      "description": "Lista os templates disponíveis",
      "parameters": {
        "type": "object",
        "properties": {},
        "required": []
      }
    }
  ]
}
```

**Conversão de tools (OpenAI → Responses API):**

```
Entrada: {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
Saída:   {"type": "function", "name": ..., "description": ..., "parameters": ...}
```

---

#### Formato de mensagens — Responses API

O campo `input` usa um formato diferente de `messages`:

| LangChain / Role   | Formato no `input[]`                                                                             |
| :----------------- | :----------------------------------------------------------------------------------------------- |
| `system`           | `{"role": "system",    "content": [{"type": "input_text",  "text": "..."}]}`                     |
| `user`             | `{"role": "user",      "content": [{"type": "input_text",  "text": "..."}]}`                     |
| `assistant`        | `{"role": "assistant", "content": [{"type": "output_text", "text": "..."}]}`                     |
| `function_call`    | `{"type": "function_call", "call_id": "...", "name": "...", "arguments": "..."}` ← **item raiz** |
| `tool` (resultado) | `{"type": "function_call_output", "call_id": "...", "output": "..."}` ← **item raiz**            |

> **Crítico:** `function_call` e `function_call_output` são **itens de nível raiz**
> no array `input`, **não** blocos dentro de `content[]`. A API retorna HTTP 400
> se colocados dentro de `content[]`.

**Exemplo de segundo turno com tool_call:**

```json
{
  "model": "gpt-5.4",
  "input": [
    { "role": "user", "content": [{ "type": "input_text", "text": "liste templates" }] },
    { "type": "function_call", "call_id": "call_abc123", "name": "listar_templates", "arguments": "{}" },
    { "type": "function_call_output", "call_id": "call_abc123", "output": "[\"fapi.pptx\"]" }
  ],
  "stream": true,
  "tools": ["..."]
}
```

---

#### Parsing do Stream SSE — /responses

Os eventos SSE têm campo `"type"` explícito (diferente do formato `/chat/completions`):

```
data: {"type": "response.created", ...}
data: {"type": "response.output_item.added", ...}
data: {"type": "response.output_text.delta", "delta": "Olá"}
data: {"type": "response.output_text.delta", "delta": "!"}
data: {"type": "response.function_call_arguments.delta", "delta": "{\"", "item_id": "..."}
data: {"type": "response.function_call_arguments.done", "arguments": "{\"frutas\":[...]}", "item_id": "..."}
data: {"type": "response.output_item.done", "item": {"type": "function_call", "call_id": "call_abc", "name": "fn", "arguments": "{...}", "status": "completed"}}
data: {"type": "response.completed", ...}
```

**Eventos relevantes:**

| Evento                                   | Ação                                                                                             |
| :--------------------------------------- | :----------------------------------------------------------------------------------------------- |
| `response.output_text.delta`             | Acumular `event.delta` (texto do assistente)                                                     |
| `response.output_item.done`              | Se `item.type == "function_call"` → capturar tool call completo (`name`, `call_id`, `arguments`) |
| `response.completed`                     | Finalizar o stream                                                                               |
| `response.function_call_arguments.delta` | Ignorar — usar `output_item.done` para o resultado final                                         |
| Outros                                   | Ignorar                                                                                          |

**Estrutura do tool_call capturado:**

```json
{
  "name": "listar_templates",
  "call_id": "call_abc123",
  "arguments": "{}"
}
```

---

## Parte 4 — Loop de Ferramentas (Agente ReAct)

O loop é idêntico nos dois endpoints, apenas com diferença no formato do histórico.

**Limite:** sem limite fixo no lado da API — o LangChain ReAct gerencia os turnos.

### Fluxo por turno

**Turno 1 — modelo solicita tool:**

1. Enviar `messages`/`input` + `tools`
2. Modelo responde com tool_calls (via SSE)
3. Extrair `name`, `id`/`call_id` e `arguments` de cada tool call

**Turno 2 — enviar resultado da tool:**

Para `/chat/completions`, adicionar ao array `messages`:

```json
{ "role": "assistant", "content": null, "tool_calls": [{ "id": "call_abc", "type": "function", "function": { "name": "fn", "arguments": "{}" } }] }
{ "role": "tool", "tool_call_id": "call_abc", "content": "resultado aqui" }
```

Para `/responses`, adicionar ao array `input` como **itens raiz**:

```json
{ "type": "function_call", "call_id": "call_abc", "name": "fn", "arguments": "{}" }
{ "type": "function_call_output", "call_id": "call_abc", "output": "resultado aqui" }
```

**Repetir** até o modelo responder sem tool_calls (`finish_reason: "stop"` ou `response.completed` sem function_call).

---

## Parte 5 — Tratamento de Erros HTTP

| Status | Causa                                                            | Ação recomendada                           |
| :----- | :--------------------------------------------------------------- | :----------------------------------------- |
| `400`  | Formato de body inválido (ex: function_call dentro de content[]) | Verificar estrutura do input               |
| `401`  | Token inválido ou expirado                                       | Refazer Device Flow (`generate-pptx auth`) |
| `403`  | Sem permissão / conta sem assinatura                             | Verificar assinatura ativa do Copilot      |
| `429`  | Rate limit atingido                                              | Aguardar e tentar novamente                |

**Debugging comum:**

- **400 na Responses API**: `function_call` ou `function_call_output` provavelmente estão dentro de `content[]` em vez de serem itens raiz do `input`.
- **400 "model is not accessible via /chat/completions"**: modelo GPT-5+ enviado para o endpoint errado. Usar `/responses`.
- **401 no Chat**: `oauth_token` inválido ou expirado. Executar `uv run generate-pptx auth`.
- **Modelos vazios após auth**: conta GitHub sem assinatura ativa do Copilot.

---

## Parte 6 — Thinking / Reasoning (Modelos Claude)

A API do Copilot implementa o "pensamento" (reasoning) de forma proprietária, diferente do padrão oficial da Anthropic ou OpenAI.

### 6.1 Ativando o Thinking

Para ativar o thinking nos modelos Claude (ex: `claude-3-5-sonnet`), o parâmetro deve ser passado **diretamente na raiz** do payload JSON como `thinking_budget`.

**Request:**

```json
{
  "messages": [{ "role": "user", "content": "Pense sobre este problema..." }],
  "model": "claude-3-5-sonnet-20241022",
  "stream": true,
  "thinking_budget": 4000
}
```

> **Atenção:** Muitas bibliotecas (como o LangChain) tentam enviar um objeto aninhado `{"thinking": {"thinking_budget": 4000}}`. O Copilot não reconhece esse formato e a requisição pode falhar silenciosamente ou resultar num erro 400.

### 6.2 Parsing do Stream de Thinking

O conteúdo do pensamento é retornado na chave `reasoning_text` dentro de `delta`. Esse conteúdo chega antes do texto de resposta (`content`).

```
data: {"choices":[{"delta":{"reasoning_text":"Vou pensar sobre isso.\n"}}]}
data: {"choices":[{"delta":{"reasoning_text":"O passo um é calcular X.\n"}}]}
data: {"choices":[{"delta":{"content":"O resultado é Y.", "reasoning_opaque":"eyJhb..."}}]}
```

- `reasoning_text`: String legível para exibir na interface como "Pensando...".
- `reasoning_opaque`: Token criptografado emitido quando o pensamento termina ou no mesmo chunk do `content`. **Crítico para multi-turn**.

### 6.3 Multi-turn com Thinking (reasoning_opaque)

Para que o modelo "lembre" do contexto do pensamento em turnos seguintes, você **não** deve reenviar o conteúdo de `reasoning_text` puro no histórico.

Em vez disso, ao receber o `reasoning_opaque` no final da geração, você o injeta como metadado no objeto da mensagem de resposta (`assistant`) da rodada anterior, seguindo o padrão Copilot.

---

## Resumo Consolidado — Todos os Endpoints

| Endpoint                                         | Método | Authorization          | Propósito                |
| :----------------------------------------------- | :----- | :--------------------- | :----------------------- |
| `https://github.com/login/device/code`           | POST   | —                      | Iniciar Device Flow      |
| `https://github.com/login/oauth/access_token`    | POST   | —                      | Polling do OAuth token   |
| `https://api.githubcopilot.com/models`           | GET    | `Bearer <oauth_token>` | Listar modelos           |
| `https://api.githubcopilot.com/chat/completions` | POST   | `Bearer <oauth_token>` | Chat GPT-4o e anteriores |
| `https://api.githubcopilot.com/responses`        | POST   | `Bearer <oauth_token>` | Chat GPT-5+              |

> Em todos os endpoints da API Copilot, o `oauth_token` é usado **diretamente**
> como Bearer — não há token intermediário.

---

## Diagrama do Fluxo Completo

```
┌──────────────────── AUTENTICAÇÃO (uma vez) ─────────────────────────────────┐
│                                                                               │
│  1. POST github.com/login/device/code                                         │
│     body: { client_id: "Ov23li8tweQw6odWQebz", scope: "read:user" }         │
│     ← { device_code, user_code, verification_uri, interval }                 │
│                                                                               │
│  2. Exibir user_code + verification_uri ao usuário                            │
│                                                                               │
│  3. LOOP (a cada `interval + 3s`, timeout 10min):                             │
│     POST github.com/login/oauth/access_token                                  │
│     body: { client_id, device_code, grant_type }                              │
│     ← { access_token: "ghu_xxx" }  →  SALVAR EM DISCO                        │
│                                                                               │
└───────────────────────────────────────────────────────────────────────────────┘

┌──────────────────── CHAT (a cada mensagem) ─────────────────────────────────┐
│                                                                               │
│  4. Detectar endpoint pelo modelo:                                            │
│     - gpt-5*  → POST api.githubcopilot.com/responses                         │
│     - outros  → POST api.githubcopilot.com/chat/completions                  │
│     Authorization: Bearer ghu_xxx  (oauth_token direto, sem troca)           │
│     Headers extras: Openai-Intent: conversation-edits                        │
│                     x-initiator: user                                         │
│                                                                               │
│  5. Parsear SSE:                                                              │
│     /chat/completions → delta.content / delta.tool_calls / delta.reasoning_text
│     /responses        → response.output_text.delta / response.output_item.done│
│                                                                               │
│  6. Se tool_calls detectados:                                                 │
│     → Executar ferramenta                                                     │
│     → Adicionar resultado ao histórico (formato depende do endpoint)         │
│     → Voltar para passo 4                                                     │
│                                                                               │
│  7. Sem tool_calls → exibir resposta final ao usuário                        │
│                                                                               │
└───────────────────────────────────────────────────────────────────────────────┘
```
