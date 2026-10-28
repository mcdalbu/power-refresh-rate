# Perfis de energia personalizados no Fedora

Guia de implantação em outra máquina, preparado em 28/10/2026.

Este documento reproduz a política usada em um Fedora 44 com GNOME 50,
Intel Core Ultra 7 256V, `intel_pstate`, Intel LPMD e GPU Intel com driver `xe`.
As tabelas EPP são personalizadas. Não são uma recomendação universal da Intel
nem uma configuração de autonomia ótima demonstrada por benchmark.

O pacote que acompanha este MD contém os programas e unidades necessários.
Os comandos abaixo devem ser executados **na máquina de destino**, dentro da
pasta extraída `implementacao-fedora`. Não é necessário manter o mesmo nome de
usuário nem copiar os instaladores antigos deste projeto.

## 1. Resultado pretendido

Ordem dos EPP: `UTIL_IDLE / UTIL_IDLE_SUSTAIN / UTIL_IDLE_BURSTY / UTIL_IDLE_GFX_BUSY`.

| Perfil | Na tomada | Na bateria |
|---|---|---|
| Economia | 208 / 96 / 96 / 160 | 224 / 160 / 128 / 192 |
| Balanceado | 192 / 64 / 64 / 128 | 208 / 128 / 96 / 160 |
| Desempenho | 0 fixo | 192 / 96 / 64 / 128 |

| Ajuste | Economia | Balanceado | Desempenho |
|---|---|---|---|
| LPMD na tomada | automático | automático | desligado |
| LPMD na bateria | automático | automático | automático |
| Máximo dos P-cores | 3 GHz | máximo do hardware | máximo do hardware |
| Máximo dos E-cores | 2 GHz | máximo do hardware | máximo do hardware |
| GPU na tomada | `base` | `base` | `base` |
| GPU na bateria | `power_saving` | `power_saving` | `base` |
| Tela, extensão opcional | 60 Hz | 120 Hz | 120 Hz |
| Turbo | permitido | permitido | permitido |

EPP vai de 0 a 255: valores menores favorecem desempenho e maiores favorecem
eficiência. Não é porcentagem, frequência fixa nem seleção de E-cores.
Os nomes dos quatro estados descrevem classificações do LPMD, que também
considera WLT e limiares gráficos; não são quatro faixas percentuais de CPU.

Os modos de firmware são escolhidos pelo PPD entre os anunciados pelo
fabricante. Neste ASUS são `quiet`, `balanced`, `performance`. Outra máquina
pode oferecer nomes e comportamentos diferentes.

## 2. Componentes

- **PPD (`power-profiles-daemon`):** fornece os três perfis ao GNOME e aplica
  preferências de CPU/firmware.
- **UPower:** informa se a máquina está na bateria.
- **LPMD (`intel_lpmd`):** seleciona os estados de carga e aplica seus EPP.
- **`power-profile-lpmd.service`:** escolhe uma das seis tabelas conforme
  perfil/alimentação e reinicia o LPMD quando a tabela muda.
- **`power-profile-cpu-cap.service`:** aplica os tetos CPU em Economia e a
  política GPU conforme perfil/alimentação.
- **Thermald:** mantém o controle térmico compatível com o hardware.
- **Power Refresh Rate:** extensão GNOME opcional para 60/120 Hz.

A versão do LPMD usada na origem compartilha uma tabela entre os perfis e
não expõe recarga dessa tabela por D-Bus. O seletor espera 1,2 segundo para
agrupar eventos, respeita três segundos entre reinícios e confere a tabela
periodicamente a cada 15 segundos. A troca entre os quatro estados de carga
acontece no próprio LPMD, sem reiniciar o serviço.

## 3. Verificar a compatibilidade antes de instalar

```bash
cat /etc/fedora-release
uname -r
lscpu
gnome-shell --version
cat /sys/devices/system/cpu/cpufreq/policy0/scaling_driver
cat /sys/devices/system/cpu/cpufreq/policy0/energy_performance_available_preferences
cat /sys/bus/event_source/devices/cpu_core/cpus
cat /sys/bus/event_source/devices/cpu_atom/cpus
cat /sys/firmware/acpi/platform_profile_choices
```

Esta implementação de CPU foi feita para Intel híbrido com `intel_pstate`
e identificação P/E pelas listas `cpu_core/cpus` e `cpu_atom/cpus`. Se essas
interfaces não existirem, não instale o controlador de limites sem adaptá-lo.
O grupo `cpu_atom` pode reunir mais de uma classe de núcleos eficientes em
outros modelos; o script não diferencia E de LP-E dentro desse grupo.

LPMD exige suporte do processador/kernel e um XML apropriado. Em AMD ou Intel
não suportado, mantenha o PPD e omita LPMD/seletor; os ajustes Intel não se
transferem automaticamente. A extensão de tela pode ser usada separadamente.

Para a GPU, descubra as interfaces reais:

```bash
find -L /sys/bus/pci/drivers/xe -path '*/freq0/power_profile' -print -exec cat {} \;
```

A saída precisa anunciar `base` e `power_saving`. Sem essa interface, omita
`--xe-gpu-policy` das duas linhas `ExecStart`/`ExecStopPost` da unidade CPU.
Não aplique esses caminhos a GPUs AMD, NVIDIA ou Intel com driver `i915`.

## 4. Registrar o estado anterior

Use a mesma sessão de terminal nos passos que usam `BKP` e `TARGET_XML`.

```bash
BKP="$HOME/backup-energia-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BKP"
rpm -q tuned tuned-ppd power-profiles-daemon intel-lpmd thermald > "$BKP/pacotes.txt" 2>&1
systemctl is-enabled tuned tuned-ppd power-profiles-daemon intel_lpmd thermald > "$BKP/habilitados.txt" 2>&1
systemctl is-active tuned tuned-ppd power-profiles-daemon intel_lpmd thermald > "$BKP/ativos.txt" 2>&1
sudo cp -a /etc/intel_lpmd "$BKP/" 2>/dev/null || true
printf '%s\n' "$BKP"
```

Os comandos de consulta podem retornar código diferente de zero para pacotes
ou serviços ausentes; isso é esperado. Guarde o caminho do backup.

Este procedimento pressupõe que os dois controladores personalizados ainda
não estão instalados. Se já existirem, preserve também suas unidades,
programas e `/etc/power-profile-lpmd` antes de qualquer substituição.

## 5. Instalar PPD e dependências

Em Fedora tradicional com DNF, se `tuned-ppd` estiver instalado, troque a ponte:

```bash
sudo dnf swap tuned-ppd power-profiles-daemon
```

Se ele não estiver instalado:

```bash
sudo dnf install power-profiles-daemon
```

Depois:

```bash
sudo dnf install intel-lpmd thermald upower python3 python3-gobject-base
/usr/bin/python3 -I -c 'from gi.repository import Gio, GLib; import gi; gi.require_version("GLibUnix", "2.0"); from gi.repository import GLibUnix; print("PyGObject OK")'
```

Revise a transação do DNF. Este roteiro não cobre diretamente instalações
atômicas como Silverblue, nas quais a instalação de pacotes segue outro fluxo.

Desative o TuneD se ele estiver instalado; mantenha um único gerenciador
principal de perfis:

```bash
sudo systemctl disable --now tuned.service
sudo systemctl mask tuned.service
```

Se a unidade não existir, omita esses dois comandos. Examine também TLP,
auto-cpufreq ou outros controladores já instalados; resolva sobreposições
antes de ativar esta implementação. Não execute vários escritores de EPP ou
máximos CPU ao mesmo tempo.

```bash
sudo systemctl enable --now power-profiles-daemon.service upower.service
sudo systemctl enable --now thermald.service
powerprofilesctl list
```

Não configure manualmente o governor `performance` para todos os perfis.
Nesta implementação, PPD usa `powersave` do `intel_pstate` e ajusta EPP;
esse governor permite turbo e variação de desempenho.

## 6. Selecionar o XML correto do LPMD

```bash
ls /etc/intel_lpmd
sudo systemctl start intel_lpmd.service
journalctl -b -u intel_lpmd.service --no-pager
```

Verifique nos logs qual configuração corresponde à família/modelo/topologia
da CPU e se o LPMD inicia sem erro de suporte. Não use `--ignore-platform-check`
para contornar incompatibilidade.

**O arquivo `epp-dinamico/template.xml` do pacote é uma referência do modelo
189 e um fixture dos testes; não deve ser instalado em outro modelo.**
Use o XML apropriado da máquina de destino. Exemplo, somente para família 6,
modelo 189:

```bash
TARGET_XML=/etc/intel_lpmd/intel_lpmd_config_F6_M189.xml
```

Substitua o caminho acima pelo identificado na sua máquina e preserve-o:

```bash
sudo cp -a "$TARGET_XML" "$BKP/active-lpmd.xml"
printf '%s\n' "$TARGET_XML" > "$BKP/target-xml.txt"
```

O seletor fornecido exige os quatro estados com nomes exatos `UTIL_IDLE`,
`UTIL_IDLE_SUSTAIN`, `UTIL_IDLE_BURSTY`, `UTIL_IDLE_GFX_BUSY`, e as opções
`lp_mode_epp`, `PowersaverDef`, `BalancedDef`, `PerformanceDef`. Se o XML do
modelo não tiver essa estrutura, é necessário adaptar o seletor e revisar
os estados, em vez de substituir o XML pelo do modelo 189.

Adapte **a cópia local** do programa para o caminho selecionado:

```bash
/usr/bin/python3 - "$TARGET_XML" <<'PY'
from pathlib import Path
import sys
p = Path('epp-dinamico/power-profile-lpmd.py')
s = p.read_text()
old = "XML = Path('/etc/intel_lpmd/intel_lpmd_config_F6_M189.xml')"
assert old in s, 'Cópia já adaptada; confira a constante XML manualmente'
p.write_text(s.replace(old, f'XML = Path({sys.argv[1]!r})'))
PY
```

## 7. Instalar as tabelas e o seletor

O JSON fornecido contém exatamente as seis tabelas da seção 1.

```bash
sudo install -d -m 0755 /etc/power-profile-lpmd /usr/local/libexec
sudo install -m 0644 epp-dinamico/config.json /etc/power-profile-lpmd/config.json
sudo install -m 0644 "$TARGET_XML" /etc/power-profile-lpmd/template.xml
sudo install -m 0755 epp-dinamico/power-profile-lpmd.py /usr/local/libexec/power-profile-lpmd.py
sudo install -m 0644 epp-dinamico/power-profile-lpmd.service /etc/systemd/system/
sudo install -d -m 0755 /etc/systemd/system/intel_lpmd.service.d
sudo install -m 0644 epp-dinamico/50-profile-selection.conf /etc/systemd/system/intel_lpmd.service.d/
```

O template mantém as decisões específicas do modelo, como máscaras de CPU,
WLT, limiares e polling. O seletor altera somente os EPP dos quatro estados,
os modos por perfil e `lp_mode_epp=-1`.

Valide a estrutura das seis combinações antes de ativar:

```bash
/usr/bin/python3 -I - <<'PY'
import importlib.util, json
from pathlib import Path
spec = importlib.util.spec_from_file_location('selector', '/usr/local/libexec/power-profile-lpmd.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
base = Path('/etc/power-profile-lpmd')
config = json.loads((base/'config.json').read_text())
for profile in m.PROFILES:
    for battery in (False, True):
        m.render((base/'template.xml').read_bytes(), config, profile, battery)
print('Seis combinações XML válidas estruturalmente')
PY
```

Isso não substitui a validação do parser/suporte real do LPMD.

## 8. Manter turbo permitido

Este exemplo pressupõe que `/sys/devices/system/cpu/intel_pstate/no_turbo`
existe. O valor **0** significa turbo permitido; não force frequência máxima.

```bash
sudo tee /etc/systemd/system/intel_lpmd.service.d/60-turbo-enabled.conf >/dev/null <<'EOF'
[Service]
ExecStartPost=/usr/bin/python3 -I -c "from pathlib import Path; Path('/sys/devices/system/cpu/intel_pstate/no_turbo').write_text('0')"
EOF
```

O drop-in aplica a preferência ao iniciar/reiniciar LPMD. Outros controles
podem alterá-la; inclua sua leitura na validação de cada perfil.

## 9. Instalar limites CPU e política GPU

Antes, confira que as frequências mínimas não excedem os tetos desejados e
que não há outro limite personalizado a preservar:

```bash
for p in /sys/devices/system/cpu/cpufreq/policy*; do
    echo "$p"
    cat "$p/related_cpus" "$p/scaling_min_freq" "$p/scaling_max_freq" "$p/cpuinfo_max_freq"
done
```

O serviço restaura os máximos anunciados pelo hardware fora de Economia e
ao parar. Ele não preserva máximos personalizados de outro controlador.

```bash
sudo install -m 0755 cpu-cap/power-profile-cpu-cap.py /usr/local/libexec/power-profile-cpu-cap.py
```

Edite `cpu-cap/power-profile-cpu-cap.service` antes de copiá-lo:

1. Os parâmetros `--max-khz 3000000` e `--efficiency-max-khz 2000000`
   representam 3 e 2 GHz; adapte-os à política desejada e ao hardware.
2. Se houver GPU Xe compatível, substitua as linhas `ReadWritePaths` da GPU
   pelos caminhos **reais resolvidos** de cada `power_profile` encontrados no
   passo 3. Na origem, eles usam o PCI `0000:00:02.0`, tile0, gt0 e gt1.
3. Sem GPU compatível, remova `--xe-gpu-policy` de `ExecStart` e `ExecStopPost`
   e remova as duas linhas `ReadWritePaths` referentes à GPU.
4. Preserve `After=dbus.service`. Não adicione `After=power-profiles-daemon.service`
   sem revisar o grafo: no Fedora de referência, PPD inicia após
   `multi-user.target`, enquanto estes controladores são puxados pelo mesmo
   alvo. Essa dependência adicional criaria um ciclo.

O script detecta os grupos P/E pelas interfaces do kernel; não codifique
CPUs 0–3/4–7 presumindo que a numeração da outra máquina é igual.

```bash
sudo install -m 0644 cpu-cap/power-profile-cpu-cap.service /etc/systemd/system/
sudo restorecon -RF /etc/power-profile-lpmd /etc/systemd/system/intel_lpmd.service.d
sudo restorecon -F /usr/local/libexec/power-profile-lpmd.py /usr/local/libexec/power-profile-cpu-cap.py /etc/systemd/system/power-profile-lpmd.service /etc/systemd/system/power-profile-cpu-cap.service
sudo systemctl daemon-reload
sudo systemd-analyze verify --man=no /etc/systemd/system/power-profile-lpmd.service /etc/systemd/system/power-profile-cpu-cap.service intel_lpmd.service multi-user.target
sudo systemctl enable --now intel_lpmd.service power-profile-lpmd.service power-profile-cpu-cap.service
```

Em uma máquina sem suporte ao controlador CPU, omita sua unidade, mas o
seletor LPMD ainda requer seu próprio suporte de hardware. Sem LPMD suportado,
não ative o seletor. Ative os componentes por camadas, validando cada uma.

## 10. Instalar a extensão GNOME opcional

O pacote inclui `gnome/power-refresh-rate@mcda.local.shell-extension.zip`.
Esta versão foi feita para **GNOME Shell 50 em Wayland**, painel interno com
60/120 Hz na mesma resolução e configuração de VRR, e escala aceita pelo
Mutter nos dois modos. Em outra versão do Shell, adapte e teste a extensão;
alterar somente `shell-version` não comprova compatibilidade.

Como usuário da sessão gráfica, sem `sudo`:

```bash
gnome-extensions install --force gnome/power-refresh-rate@mcda.local.shell-extension.zip
```

Saia e entre na sessão. Depois:

```bash
gnome-extensions enable power-refresh-rate@mcda.local
gnome-extensions prefs power-refresh-rate@mcda.local
```

Se já houver um serviço antigo `power-refresh-rate.service`, desabilite-o
antes de ativar a extensão. Não mantenha dois controladores da mesma tela.

A extensão preserva escala, resolução, VRR e monitores externos. A troca
física de modo pode apagar brevemente a tela. Ela não modifica o intervalo VRR.

**Não copie a biblioteca nativa de escala de 180% desta máquina.** Ela depende
da versão exata do Mutter e é separada da extensão. Comece com uma escala
que a outra máquina já oferece e aceita. Se precisar de 180% não anunciado,
essa adaptação exige uma validação própria do compositor; não faz parte da
implantação genérica deste pacote.

Projeto da extensão, privado e sujeito a acesso autorizado:
[power-refresh-rate](https://github.com/mcdalbu/power-refresh-rate).

## 11. Validar no hardware de destino

```bash
systemctl status power-profiles-daemon intel_lpmd power-profile-lpmd power-profile-cpu-cap thermald
journalctl -b -u power-profile-lpmd -u power-profile-cpu-cap -u intel_lpmd --no-pager
powerprofilesctl get
busctl get-property org.freedesktop.UPower /org/freedesktop/UPower org.freedesktop.UPower OnBattery
cat /sys/devices/system/cpu/intel_pstate/no_turbo
```

Teste Economia, Balanceado e Desempenho **na tomada e na bateria**, deixando
alguns segundos para a seleção e o LPMD estabilizarem:

```bash
powerprofilesctl set power-saver
# Inspecione os valores abaixo; depois repita com balanced e performance.
for p in /sys/devices/system/cpu/cpufreq/policy*; do
    echo "$p"
    cat "$p/energy_performance_preference" "$p/scaling_max_freq"
done
```

Confirme em cada combinação:

- o log do seletor indica o perfil e `ac`/`battery` corretos;
- o XML ativo contém o banco correspondente;
- o EPP observado corresponde ao estado escolhido pelo LPMD;
- em Desempenho na tomada, EPP é `performance`/0 e LPMD está fora do modo AUTO;
- os tetos CPU só se aplicam em Economia, e turbo continua permitido;
- os atributos GPU indicam `[power_saving]` ou `[base]` conforme a tabela;
- a extensão altera 60/120 Hz sem mudar a escala;
- suspensão/retomada e um reboot preservam o funcionamento.

Um EPP pode aparecer como número ou nome, por exemplo `performance` para 0,
`balance_performance` para 128 e `balance_power` para 192. Não espere observar
os quatro EPP em uma única leitura: o LPMD aplica o estado detectado.

Os testes incluídos podem ser executados sem alterar a sessão real:

```bash
/usr/bin/python3 -m unittest discover -s cpu-cap -p test_limits.py
/usr/bin/python3 -m unittest discover -s epp-dinamico -p test_selector.py
```

Os testes do seletor usam o XML de referência incluído como fixture. O teste
de falha imprime uma mensagem simulada de `systemctl`, esperada no teste.
Uma aprovação nesses testes não comprova suporte do outro processador.

Para avaliar eficiência, compare a mesma tarefa, brilho, tela, rede e condição
térmica. Registre tempo e energia por tarefa, além do consumo total na bateria.
Não estime ganhos percentuais de autonomia apenas a partir do EPP.

## 12. Editar as tabelas depois

Edite `/etc/power-profile-lpmd/config.json`, preservando inteiros de 0 a 255
e quatro valores por tabela. Em seguida:

```bash
sudo systemctl restart power-profile-lpmd.service
```

Não edite o XML ativo como configuração permanente: o seletor o substitui.
A mudança de alimentação não exige reiniciar a extensão GNOME ou o PPD.
Parar apenas o seletor mantém o último banco; isso não restaura a política
anterior ao projeto.

## 13. Remover esta instalação

Em uma instalação nova feita por este roteiro:

```bash
sudo systemctl disable --now power-profile-lpmd.service power-profile-cpu-cap.service
sudo systemctl stop intel_lpmd.service
sudo cp -a "$BKP/active-lpmd.xml" "$TARGET_XML"
sudo rm /etc/systemd/system/power-profile-lpmd.service /etc/systemd/system/power-profile-cpu-cap.service
sudo rm /usr/local/libexec/power-profile-lpmd.py /usr/local/libexec/power-profile-cpu-cap.py
sudo rm /etc/systemd/system/intel_lpmd.service.d/50-profile-selection.conf /etc/systemd/system/intel_lpmd.service.d/60-turbo-enabled.conf
sudo rm /etc/power-profile-lpmd/config.json /etc/power-profile-lpmd/template.xml
sudo rmdir /etc/power-profile-lpmd
sudo restorecon -F "$TARGET_XML"
sudo systemctl daemon-reload
```

Antes, recupere `BKP` e `TARGET_XML` se tiver aberto outro terminal. Remova
somente arquivos criados pelo roteiro; se já existiam antes, restaure as
cópias anteriores em vez de removê-los. O `ExecStopPost` do controlador CPU
restaura GPU `base` e máximos CPU ao parar, quando as interfaces estão disponíveis.

Consulte `ativos.txt`, `habilitados.txt` e `pacotes.txt` do backup para devolver
os serviços ao estado original. Se LPMD não era usado antes, desabilite-o;
se era usado, restaure seu estado anterior. Faça o mesmo para Thermald/UPower/PPD
quando necessário.

Se a máquina usava TuneD com `tuned-ppd`, restaure esse backend deliberadamente:

```bash
sudo systemctl disable --now power-profiles-daemon.service
sudo dnf swap power-profiles-daemon tuned-ppd
sudo systemctl unmask tuned.service
sudo systemctl enable --now tuned.service tuned-ppd.service
```

Use esse último bloco apenas se esse era o backend anterior. Confirme os nomes
das unidades fornecidas pelos pacotes da sua versão. Não execute PPD e a ponte
`tuned-ppd` juntos. A extensão pode ser desativada separadamente:

```bash
gnome-extensions disable power-refresh-rate@mcda.local
```

## Fontes

- [Pacote Intel LPMD no Fedora](https://packages.fedoraproject.org/pkgs/intel-lpmd/intel-lpmd/).
- [Pacote power-profiles-daemon no Fedora](https://packages.fedoraproject.org/pkgs/power-profiles-daemon/power-profiles-daemon/).
- [Intel LPMD: funcionamento e templates por plataforma](https://github.com/intel/intel-lpmd).
- [Kernel: intel_pstate e preferências HWP/EPP](https://docs.kernel.org/admin-guide/pm/intel_pstate.html).
- [Código do driver Xe: política de energia nativa](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/xe/xe_guc_pc.c).

Os arquivos de apoio deste pacote são uma cópia dos controladores locais.
Os scripts antigos de instalação/reversão, com caminhos deste usuário e
backups específicos desta máquina, não são usados neste roteiro.
