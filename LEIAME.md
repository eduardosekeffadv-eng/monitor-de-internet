# Monitor de Internet — versão 2.2.0

Estação local de monitoramento da conexão de internet para Windows 11 —
Starlink, fibra, cabo, rádio, 4G/5G ou outro tipo. Mede a conexão a cada
segundo, detecta e registra quedas, e mostra tudo em um painel no Google
Chrome em `http://localhost:8000`.

Tudo roda no seu computador: sem servidores externos, sem CDN, sem conta,
sem administrador e sem teste de velocidade.

---

## Instalação (uma vez)

1. Tenha o **Python 3.10 ou mais recente** instalado
   (https://www.python.org/downloads/ — marque *Add python.exe to PATH*).
2. Extraia esta pasta em um local definitivo, por exemplo
   `C:\Users\SeuNome\MonitorDeInternet` (evite a pasta Downloads).
3. Dê dois cliques em **`instalar.bat`**.
   Ele cria o ambiente Python em `.venv`, instala as dependências e testa a
   medição nativa do Windows. É a única etapa que precisa de internet.

## Atualizando

**Da versão 2.x para a 2.2:** rode o `parar_monitoramento.bat`, extraia esta
versão **por cima** da pasta atual (substituindo os arquivos) e rode o
`instalar.bat` (ele instala o componente de leitura da antena Starlink) e
depois o `iniciar_monitoramento.bat`. A pasta `data` (histórico
e configurações) é preservada e atualizada automaticamente. No Chrome, aperte
Ctrl+F5 uma vez.

### Da versão 1.x (Starlink Monitor)

1. Rode o `parar_monitoramento.bat` da versão antiga.
2. Extraia esta versão em uma pasta nova.
3. Copie a pasta **`data`** da versão antiga para dentro da nova (ela guarda
   todo o histórico e as configurações).
4. Rode o `instalar.bat` na pasta nova e depois o `iniciar_monitoramento.bat`.

O histórico é preservado e convertido automaticamente; o tipo de conexão
fica definido como **Starlink**. A pasta antiga pode ser apagada depois.

## Uso

| Arquivo | O que faz |
|---|---|
| `iniciar_monitoramento.bat` | Inicia o monitoramento em segundo plano e abre o painel no Chrome. Se já estiver rodando, só abre o painel. |
| `parar_monitoramento.bat` | Encerra o monitoramento com segurança. |

* O monitoramento **continua com o Chrome fechado** e com a janela preta
  fechada. Reabra o painel quando quiser pelo `iniciar_monitoramento.bat`
  ou acessando `http://localhost:8000`.
* Na primeira execução, o painel pergunta o **tipo de conexão de internet**
  (veja abaixo). Para trocar depois, clique no botão **Conexão** no topo.
* Na primeira abertura de cada sessão, o painel pergunta o **modo de
  conexão do computador** (cabo direto, Wi-Fi direto ou Wi-Fi via
  roteador). Para trocar depois, clique no cartão **Modo de conexão**.
* O painel funciona normalmente durante uma queda da internet (todos os
  arquivos são locais).

---

## Páginas

### Painel
O status, os indicadores do dia e o gráfico de barras em tempo real.

* **Pausar / AO VIVO** (ao lado das janelas de 1, 3, 5 e 10 min): **Pausar**
  congela o gráfico para você analisar as barras com calma — o monitoramento
  continua normalmente. **AO VIVO** salta de volta para o momento atual,
  como numa transmissão ao vivo. Atalho: **barra de espaço**.
* **Barra de rolagem** abaixo do gráfico: arraste (ou clique no trilho) para
  ver o que já passou, até 30 minutos atrás. O gráfico pausa sozinho e o
  monitoramento continua; **AO VIVO** volta ao tempo atual. Com o gráfico
  pausado, a roda do mouse sobre ele também navega no tempo. Para períodos
  maiores, use o **Histórico**.
* **Zoom** (lista com a lupa, no topo de todas as páginas): ajusta o tamanho
  de tudo na tela, de 75% a 125%. A escolha fica salva no navegador.

### Histórico
Análise de qualquer período: últimos 5 min, 15 min, 1 h, 6 h, 24 h, 7 dias,
30 dias ou um intervalo personalizado.

* Indicadores do período (disponibilidade, interrupções, tempo sem conexão,
  maior queda, microquedas, latência, perda e classificação de estabilidade).
* Gráfico de latência e quedas e gráfico de perda de pacotes. Para períodos
  longos, as barras são médias (por 10 s, 1 min, 5 min, 30 min, 2 h ou dia,
  conforme o período); os registros originais continuam no banco. Nas médias,
  um ▼ vermelho no topo indica interrupção naquele intervalo e um ▼ laranja,
  microqueda.
* **Horários com mais problemas**: mapa de calor dia × hora (indisponibilidade,
  quedas, perda ou latência) e os três piores horários.
* **Comparativo por modo de conexão** (cabo × Wi-Fi) e **comparação entre os dias**.
* **Linha do tempo de eventos**, com filtros (quedas, instabilidade,
  monitoramento, alterações) e detalhes de cada queda ao clicar.
* **Relatório deste período**: abre o relatório completo do período escolhido.

### Relatórios
Relatórios **diário**, **semanal** (segunda a domingo), **mensal** ou de
**período personalizado**, em HTML:

* Resumo em texto, disponibilidade e classificação da estabilidade.
* Gráficos de latência e perda, comparação entre dias e entre modos de
  conexão, mapa de calor por horário, indicadores de estabilidade.
* Linha do tempo das quedas e **lista detalhada das interrupções** (data,
  início e término com a margem de precisão, duração, diagnóstico, estado do
  roteador e do equipamento do provedor, modo/sinal Wi-Fi, latência antes e
  depois) — suficiente para documentar problemas junto ao provedor.
* **Baixar arquivo HTML**: arquivo único e independente (dados, gráficos,
  fontes e a biblioteca de gráficos embutidos) — abre em qualquer computador,
  sem o Monitor de Internet e sem internet.
* **Imprimir / salvar PDF**: versão de impressão com fundo claro, em A4.

### Configurações
Todos os parâmetros ajustáveis pelo navegador, aplicados na hora (sem
reiniciar, exceto a porta): tipo de conexão, destinos monitorados, intervalo
e tempo limite, limites de latência e de perda, ciclos para confirmar uma
interrupção, sinal Wi-Fi fraco, retenção dos dados, impedir suspensão,
**iniciar com o Windows** (sem administrador) e janela padrão do gráfico.
Valores fora dos limites são recusados ou ajustados, com aviso.

---

## Tipo de conexão

| Tipo | Equipamento do provedor | Endereço padrão | Limites recomendados (normal / elevada) |
|---|---|---|---|
| Starlink | Antena Starlink | 192.168.100.1 | 100 / 250 ms |
| Fibra óptica | Modem/ONT de fibra | 192.168.100.1 | 60 / 150 ms |
| Cabo coaxial | Modem a cabo | 192.168.100.1 | 60 / 150 ms |
| Via rádio | Rádio do provedor | — | 80 / 200 ms |
| 4G/5G | Modem 4G/5G | — | 120 / 300 ms |
| Outro satélite (HughesNet, Viasat…) | Modem de satélite | — | 800 / 1200 ms |
| Outro | Equipamento do provedor | — | 100 / 250 ms |

* O **equipamento do provedor** é o aparelho entre o seu roteador e a rede
  da operadora. Se ele for alcançável pela sua rede, o diagnóstico separa
  "falha entre o roteador e o equipamento" de "equipamento respondendo, mas
  sem internet". Se não for, o monitor desativa essa verificação sozinho,
  sem alertas. O endereço pode ser alterado (ou deixado em branco) no
  seletor de tipo de conexão.
* Ao escolher o tipo, os **limites de latência recomendados** podem ser
  aplicados (opção marcada por padrão). Satélites geoestacionários têm
  latência naturalmente alta (~600 ms), por isso os limites são diferentes.
* O **nome do provedor ou plano** é opcional (ex.: "Vivo Fibra 500 MB") e
  aparece no topo do painel.
* Toda troca de tipo fica registrada como evento, com data e hora.

## Dados da própria antena Starlink (versão 2.2)

Com o tipo de conexão **Starlink**, o monitor também consulta a própria
antena pela interface local de diagnóstico dela (`192.168.100.1`, porta 9200 —
a mesma usada pelo aplicativo da Starlink). É **somente leitura**: nenhum
comando que altere a antena é enviado, e a localização não é consultada.

O que aparece:

* **Painel → cartão "Antena Starlink":** estado (conectada / procurando
  satélite / obstruída…), latência e perda medidas pela antena até a rede da
  Starlink, obstrução, consumo, tempo ligada, tráfego e alertas (calor,
  água, mastro torto, sinal abaixo do esperado etc.). Também mostra as quedas
  que a antena registrou nas últimas 24 h, agrupadas por **motivo**.
* **Mapa do céu:** o mapa de obstruções que a antena monta sozinha, como no
  aplicativo da Starlink.
* **Motivo de cada queda:** cada interrupção ou microqueda do monitor que
  coincide com uma queda registrada pela antena ganha a etiqueta com o motivo
  informado por ela (tolerância de 5 s para diferença de relógio):

| Motivo | Lado | Significado |
|---|---|---|
| Troca de satélite | Rede Starlink | falha na passagem de um satélite para outro |
| Sem resposta da rede Starlink | Rede Starlink | antena ok, mas a rede não respondeu |
| Sem satélite disponível / nenhum satélite visível | Rede Starlink | falta de cobertura ou capacidade no momento |
| Obstrução | Instalação | algo bloqueou a visão do céu |
| Antena reiniciando, reinício do cabo, temperatura, modo de economia… | Instalação | causas na própria antena ou na alimentação |

* **Histórico e relatórios** ganham a seção **"Quedas segundo a antena
  Starlink"**, que separa as quedas da rede Starlink das ligadas à
  instalação, mostra quantas quedas do monitor foram explicadas pela antena
  e quantas foram tão curtas que só a antena percebeu.
* **Alertas da antena** viram eventos na linha do tempo (filtro
  *Instabilidade*).

Funciona pelo Wi-Fi ou cabo da Starlink e, na maioria dos casos, também por
um roteador próprio ligado depois dela. Se a antena não responder, o cartão
avisa e o monitor tenta de novo periodicamente — **o monitoramento normal não
depende disso**. Essa interface não é documentada oficialmente; se a Starlink
mudar algo, só esses dados extras deixam de aparecer. Pode ser desligado em
**Configurações → Tipo de conexão**.

---

## Como as medições funcionam

A cada segundo, em paralelo:

| Destino | Endereço | Para quê |
|---|---|---|
| Roteador | gateway padrão, detectado automaticamente | saúde da rede da casa |
| Cloudflare | 1.1.1.1 | conectividade externa |
| Google | 8.8.8.8 | segunda verificação externa, independente |
| Equipamento do provedor | conforme o tipo de conexão | opcional: usado só se for alcançável pela sua rede |

* A medição usa a API ICMP nativa do Windows (`IcmpSendEcho`), sem abrir o
  `ping.exe` e sem administrador.
* Se um destino deixar de responder ao ICMP, o ciclo seguinte testa ICMP e
  TCP (porta 443) **em paralelo**; qualquer resposta vale. Destinos que
  bloqueiam ICMP passam a ser medidos por TCP automaticamente.
* **Latência atual** = a menor latência entre Cloudflare e Google naquele
  ciclo (o caminho até a internet). O roteador aparece separado.

### Classificação (padrões, ajustáveis em Configurações)

| Situação | Resultado |
|---|---|
| Pelo menos um destino externo respondeu | conectado — falha de um único servidor **não** é queda |
| Todos os externos sem resposta por 1–2 ciclos | **Microqueda** (registrada à parte) |
| Todos os externos sem resposta por **3 ciclos seguidos** | **Interrupção confirmada** → OFFLINE. O início é retroativo ao 1º ciclo sem resposta |
| Perda acima de **2 %** na janela de **60 s** | **Perda de pacotes** → INSTÁVEL |
| Mediana da latência acima do **limite normal** (ex.: 100 ms na Starlink) | **Latência elevada** → INSTÁVEL |

Os horários de início e fim de quedas são mostrados com a margem real da
medição (**±1 s** com o intervalo padrão), sem inventar precisão.

### Diagnóstico provável

O sistema não presume que toda falha é do provedor:

1. Modo Wi-Fi e o computador perdeu o Wi-Fi, ou sinal fraco com o roteador
   também sem resposta → **possível problema de Wi-Fi**.
2. Computador sem rota de rede → **cabo/adaptador desconectado**.
3. Roteador sem resposta → **possível problema na rede local**.
4. Roteador respondendo, externos não → **provável falha de acesso à internet**.
   Se o equipamento do provedor estiver sendo monitorado, o diagnóstico
   separa "equipamento respondendo, sem internet" (rede da operadora; na
   Starlink, o enlace via satélite) de "equipamento sem resposta" (ligação
   entre o roteador e o equipamento).

### Períodos sem monitoramento

Computador desligado, suspenso ou programa fechado aparecem como
**Monitoramento inativo** e não entram em nenhuma estatística — nem como
tempo online, nem como queda. A disponibilidade é calculada só sobre o
tempo efetivamente medido.

---

## Limitações conhecidas (e o que foi feito)

* **Resolução de 1 ms do ICMP do Windows.** O roteador costuma responder em
  menos de 1 ms; isso aparece como `<1 ms`, nunca como falha.
* **Sinal Wi-Fi no Windows 11 24H2.** O `netsh wlan show interfaces` pode
  exigir a **Localização** ativada (Configurações › Privacidade e segurança
  › Localização). Sem ela, o sinal é registrado como indisponível e o
  painel avisa — nenhum valor é inventado.
* **Impedir suspensão** evita apenas a suspensão automática por
  inatividade. Fechar a tampa do notebook, o botão de energia ou
  "Suspender" no menu ainda suspendem o computador (esses períodos viram
  "Monitoramento inativo"). A tela pode apagar normalmente.
* **Equipamento do provedor**: com roteador próprio, ele pode ou não ser
  alcançável. O sistema testa sozinho; se não for, desativa sem alertas e
  tenta de novo a cada 10 minutos.

---

## Arquivos e dados

```
data\monitor.db        banco SQLite (WAL) com medições, eventos e agregados
data\config.json       configurações (criado na primeira execução)
data\logs\monitor.log  log de execução (rotativo, até ~25 MB)
```

* Dados brutos (1 linha por segundo, cerca de 4 MB por dia) ficam 30 dias;
  médias por minuto, 1 ano; eventos (quedas, microquedas etc.) ficam
  permanentemente.
* Um encerramento inesperado perde no máximo o último segundo. Na próxima
  execução, o que ficou aberto é fechado na última medição registrada e
  marcado como tal.
* O painel só aceita conexões do próprio computador (`127.0.0.1`).

## Arquivo de configuração

Tudo pode ser ajustado pela página **Configurações**. As mesmas opções ficam
em `data\config.json`, que também pode ser editado à mão com o monitoramento
parado (valores inválidos são substituídos pelos padrões automaticamente).

Principais chaves: `interval_ms`, `timeout_ms`, `lat_normal_max_ms`,
`lat_elevated_max_ms`, `loss_threshold_pct`, `loss_window_s`,
`outage_confirm_cycles`, `wifi_weak_signal_pct`, `retention_raw_days`,
`retention_agg_days`, `prevent_sleep`, `port`, `provider_type`,
`provider_name` e a lista `targets`.

---

## Problemas comuns

* **"Python não encontrado"**: instale o Python marcando *Add python.exe to
  PATH* e rode o `instalar.bat` de novo.
* **"A porta 8000 está ocupada"**: outro programa usa a porta. Feche-o ou
  troque `"port"` em `data\config.json`.
* **O monitoramento não iniciou**: o `iniciar_monitoramento.bat` mostra as
  últimas linhas do log. O log completo está em `data\logs\monitor.log`.
* **Teste de ICMP falhou na instalação**: algum antivírus/firewall pode
  bloquear o ICMP do próprio computador. O monitor continua funcionando
  pelo teste TCP alternativo; se possível, libere o Python no firewall.

---

## Para desenvolvedores

* Testes automatizados (simulam perda de pacotes, microquedas, quedas,
  restabelecimento, suspensão do computador e recuperação após falha):

  ```
  .venv\Scripts\python -m pip install -r requirements-dev.txt
  .venv\Scripts\python -m pytest
  ```

* Modo demonstração com dados **fictícios**, em banco separado
  (`data\simulacao.db`) e com faixa de aviso no painel:

  ```
  .venv\Scripts\python -m app.main --simulacao --console
  ```

### Estrutura

```
app\providers.py  tipos de conexão e equipamento do provedor de cada um
app\monitor\   motor: ICMP nativo, TCP, gateway, Wi-Fi, energia, ciclo de medição, dados da antena Starlink (dish_api.py)
app\events\    detecção de eventos e diagnóstico
app\storage\   SQLite: esquema, gravação, consultas, agregação, retenção
app\services\  indicadores do painel e análise de períodos (séries, estatísticas, mapa de calor)
app\reports\   montagem dos relatórios HTML independentes
app\system\    início automático com o Windows
app\api\       servidor web (FastAPI), REST e WebSocket
web\           páginas (Painel, Histórico, Relatórios, Configurações), CSS, JS, Chart.js e fontes locais
web\report\    modelo, estilo e script dos relatórios
tests\         testes automatizados
```
