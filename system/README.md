# Complemento de sistema para as preferências GNOME

A extensão pode controlar somente a tela sem este complemento. Os controles
EPP/CPU/GPU exigem a implantação PPD/LPMD documentada em `docs/fedora-energy/README.md`
e este helper instalado como root. O complemento não instala pacotes nem
escolhe o XML do processador automaticamente.

## O que a janela edita

Cada perfil tem seções separadas para tomada/bateria:

- quatro EPP (0–255), seguindo os estados existentes do LPMD;
- máximo de P-cores e de E-cores, em MHz; **0 significa máximo do hardware**;
- GPU Intel Xe `base` ou `power_saving`;
- tela 60/120 Hz, salva por usuário na extensão;
- em Desempenho, EPP dinâmico ou EPP 0 fixo por fonte de alimentação.

Economia e Balanceado continuam usando LPMD automático. Turbo, topologia,
limiares WLT, firmware e escala do monitor não são controles deste editor.
Os valores existentes são carregados ao abrir as preferências. Alterações
CPU/EPP/GPU só são aplicadas pelo botão da página Geral; mudanças de frequência
da tela são imediatas e independem do helper. Não são aplicadas sugestões
novas de EPP automaticamente.

## Instalar sobre a implantação existente

Confirme PPD, LPMD, `power-profile-lpmd.service` e
`power-profile-cpu-cap.service` funcionando antes de executar:

```sh
sudo /usr/bin/python3 -I system/install-backend.py
```

O instalador:

1. preserva o JSON EPP existente e guarda os arquivos substituídos em
   `/var/backups/energy-trial/gnome-power-preferences-*`;
2. cria `/etc/power-profile-cpu-cap/config.json` com a política anterior:
   tetos 3/2 GHz em Economia, demais perfis sem teto extra, GPU em economia
   na bateria exceto no Desempenho, base na tomada;
3. atualiza o controlador CPU/GPU para ler esses valores por contexto;
4. instala o helper e uma ação polkit com autenticação administrativa;
5. verifica o grafo systemd e testa aplicação/restauração de uma alteração
   temporária em um banco EPP inativo.

O instalador foi feito para a implantação original. Se já utiliza tetos ou
políticas GPU diferentes, adapte `hardware-defaults.json` antes da instalação.
A unidade existente é preservada e recebe `--settings-file`, mantendo seus
caminhos GPU específicos. O instalador exige o `--xe-gpu-policy` da implantação
original; máquinas sem esse recurso precisam adaptar essa instalação.
Ele recusa sobrescrever um complemento já instalado.

Depois, instale o ZIP atualizado da extensão e saia/entre na sessão. O GNOME
Shell 50 não recarrega o JavaScript de uma extensão já importada na sessão.

## Aplicação e permissões

As preferências executam apenas:

```text
pkexec --disable-internal-agent /usr/local/libexec/power-profile-settings-helper
```

Um JSON limitado é enviado pela entrada padrão. O helper não aceita caminhos,
comandos, scripts, templates ou nomes de serviço enviados pelo usuário.
A ação polkit exige `auth_admin` para a sessão ativa; a extensão não ganha
permissão permanente para executar comandos como root.

O helper valida esquema/chaves, inteiros de EPP, limites MHz aceitos pelo
hardware, flags booleanas e os dois valores GPU. Mantém EPP 0 no Desempenho
fixo. Bloqueia chamadas concorrentes, cria backup, substitui atomicamente
somente os dois arquivos de configuração fixos e reinicia os dois controladores.
Em falha de reinício, restaura os arquivos e tenta reiniciar com a política anterior.
Os arquivos precisam ser de root, sem escrita de grupo/outros e sem link simbólico.

Arquivos instalados:

- `/usr/local/libexec/power-profile-settings-helper`
- `/usr/share/polkit-1/actions/org.mcda.power-profile-settings.policy`
- `/etc/power-profile-cpu-cap/config.json`
- `/usr/local/libexec/power-profile-cpu-cap.py` (atualizado)
- `/etc/systemd/system/power-profile-cpu-cap.service` (atualizado)

O seletor LPMD, seu template e o JSON `/etc/power-profile-lpmd/config.json`
continuam nos caminhos da implantação anterior. Para uma edição manual dos
JSON, reinicie o controlador correspondente para reler o arquivo.

## Testes

```sh
/usr/bin/python3 -m unittest discover -s system -p 'test_*.py'
make test
make test-shell
```

Os testes de helper cobrem rejeição de campos extras, EPP fora da faixa,
booleanos usados como frequência, limites incompatíveis, política GPU inválida,
EPP fixo e restauração dos dois arquivos após falha. O GNOME virtual testa o
editor com um helper simulado; não solicita autenticação nem altera a sessão real.

## Reversão do complemento

Use o diretório de backup impresso pelo instalador. Feche as preferências.
Restaure `power-profile-cpu-cap.py` e `power-profile-cpu-cap.service` desse
backup para seus caminhos anteriores, mantendo permissões de root e 0755/0644.
Depois remova o helper, sua ação polkit e o JSON de hardware criado pelo
complemento, faça `systemctl daemon-reload` e reinicie o controlador CPU/GPU.

Essa reversão volta à política CPU/GPU codificada no controlador anterior.
Os EPP editados pelo usuário permanecem no JSON do seletor; se desejar
retornar também aos EPP anteriores à instalação, restaure `epp-config.json`
do backup para `/etc/power-profile-lpmd/config.json` e reinicie o seletor.

O ZIP anterior da extensão pode ser reinstalado separadamente. Preserve as
preferências GSettings: a migração mantém as antigas frequências por perfil
até a primeira edição de uma frequência separada por tomada/bateria.

Referências: [polkit/pkexec](https://polkit.pages.freedesktop.org/polkit/pkexec.1.html)
e [preferências de extensões GNOME](https://gjs.guide/extensions/development/preferences.html).
