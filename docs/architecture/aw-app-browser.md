---
repo: architecture
path: docs/architecture/aw-app-browser.md
source: generated
edited: false
checksum: sha256:a9a29562bc8b59a8e0185a8f5cc544a68396598da14309d9923e2e9a437e181a
---
# Browser

- **repo**: aw-app-browser
- **layer**: app-container
- **technologies**: docker
- **health** (derived): planned

Chromium for AW workspaces with an interactive noVNC screen, CDP automation endpoint, workspace cookie-proxy support, and configurable window size.

## Connections
- `other` → **aw-app-proxy** — Authentication — this container's Chrome must tunnel through aw-app-proxy's CONNECT proxy, and aw-app-proxy injects/clears its cookies via CDP so it's logged in as the user

## MCP tools
_none exposed_

## Requirements
### O Host de entrada é reescrito para localhost, senão o Chrome recusa por proteção de DNS rebinding
- Given os endpoints HTTP de debug do Chrome recusam qualquer Host que não seja localhost ou um IP, e quem chama é outro container usando o nome DNS aw-app-browser:9223
- When o proxy reescreve o cabeçalho antes de repassar (repos/aw-app-browser/container/cdp_proxy.py, docstring:1-14, sobre a cabeça lida por _read_head:26)
- Then o Chrome recebe Host: localhost:9222 e aceita a requisição — um proxy TCP que só encaminha bytes não consegue satisfazer isso E continuar alcançável por hostname, que é exatamente por que este proxy entende HTTP em vez de ser um encaminhador cego. Sem a reescrita a conexão estabelece e o Chrome responde erro, o que se parece com browser fora do ar
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-browser/tests/test_cdp_proxy.py` (passing)

### As URLs da resposta de descoberta apontam de volta para o proxy, não para o loopback do Chrome
- Given a resposta JSON de descoberta traz webSocketDebuggerUrl e devtoolsFrontendUrl apontando para localhost:9222, endereço que só existe dentro do container do browser
- When o corpo é reescrito na volta (repos/aw-app-browser/container/cdp_proxy.py, trocando localhost:9222 e 127.0.0.1:9222 por EXTERNAL_HOST_PORT:22)
- Then o cliente recebe URLs com o host:porta externo e a conexão WebSocket seguinte volta por este mesmo proxy — sem isso o cliente lê a descoberta com sucesso e tenta abrir o socket direto no loopback do Chrome, que do lado dele é o próprio container, e a falha aparece um passo depois do ponto onde a causa está. O destino externo é configurável por env, com aw-app-browser:9223 como padrão
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-browser/tests/test_cdp_proxy.py` (passing)

### Depois do upgrade o proxy sai da frente e só encaminha bytes
- Given o tráfego CDP de verdade é framing WebSocket binário, que não se ganha nada em interpretar
- When o upgrade recebe a mesma reescrita de Host e em seguida entra o splice bidirecional cru (repos/aw-app-browser/container/cdp_proxy.py::_pipe:39)
- Then os dois sentidos são encaminhados em blocos de 64 KiB até o fim, e ConnectionResetError, BrokenPipeError e IncompleteReadError são engolidos com o writer fechado no finally — desconexão abrupta é o caso NORMAL aqui, não excepcional: quem pilota o browser fecha a aba, e tratar isso como erro encheria o log de ruído sobre algo que não é problema. Interpretar os frames só criaria uma segunda implementação de CDP para manter
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: _none linked_
