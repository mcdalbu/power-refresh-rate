# Power Refresh Rate

Extensão para GNOME Shell 50 que ajusta a frequência do painel interno conforme
o perfil de energia selecionado no GNOME.

| Perfil | Frequência padrão |
| --- | --- |
| Economia de energia | 60 Hz |
| Balanceado | 120 Hz |
| Desempenho | 120 Hz |

As preferências permitem escolher 60 ou 120 Hz para cada perfil. A extensão
preserva a resolução, a escala, o VRR, a posição dos monitores e a configuração
dos monitores externos. Usa sinais D-Bus e dispensa um serviço Python permanente.

## Requisitos

- GNOME Shell 50 em Wayland.
- Um serviço compatível com a API `org.freedesktop.UPower.PowerProfiles`, como
  `power-profiles-daemon`.
- Painel interno com modos de 60 e 120 Hz na resolução e configuração de VRR
  em uso. Outros alvos não são configuráveis nesta versão.
- A escala atual deve ser aceita pelo Mutter nos modos selecionados.

A extensão acompanha o perfil ativo. Ela não configura PPD, TuneD, LPMD, EPP
ou o turbo do processador.

## Instalação

Baixe o arquivo `.shell-extension.zip` da página de
[releases](https://github.com/mcdalbu/power-refresh-rate/releases) e instale:

```sh
gnome-extensions install --force power-refresh-rate@mcda.local.shell-extension.zip
```

Saia e entre na sessão para o GNOME descobrir a nova extensão. Depois:

```sh
gnome-extensions enable power-refresh-rate@mcda.local
gnome-extensions prefs power-refresh-rate@mcda.local
```

Também é possível ativar, desativar e abrir as preferências pelo aplicativo
**Extensões** do GNOME. As preferências mostram o estado e o diagnóstico de falhas.

### Instalar a partir do código

Com `make` e as ferramentas de extensões do GNOME instalados:

```sh
git clone https://github.com/mcdalbu/power-refresh-rate.git
cd power-refresh-rate
make build
make install
```

O pacote é gerado em `dist/`. O UUID é `power-refresh-rate@mcda.local`.

## Comportamento

O controlador acompanha mudanças de perfil, monitores e disponibilidade da
tela. Antes de aplicar, pede ao Mutter que valide o layout completo. A aplicação
é temporária, sem gravar uma nova configuração em `monitors.xml`.

A extensão escolhe um modo com a mesma resolução e configuração de VRR.
Se não encontrar o modo ou a escala não for aceita, preserva a configuração
atual e mostra o erro nas preferências. Desativar a extensão cancela operações
pendentes e deixa a tela no modo atual.

Trocar os modos físicos de 60 e 120 Hz pode causar um breve apagão. Esta
extensão usa essa troca de modo; não altera os limites do intervalo de VRR.

### Escalas personalizadas, incluindo 180%

A extensão preserva as escalas anunciadas e aceitas pelo Mutter; ela não
acrescenta opções de escala ao compositor. Em versões que não oferecem 180%,
essa escala precisa ser disponibilizada separadamente. Uma configuração antiga
carregada em 180% pode continuar visível enquanto a API rejeita alterações de
frequência. Nesse caso, as preferências mostram um diagnóstico específico.

O complemento nativo de escala usado na configuração local de desenvolvimento
é independente desta extensão e não faz parte deste repositório.

### Migração do serviço anterior

Se estiver usando `power-refresh-rate.service`, pare e desabilite esse serviço
antes de ativar a extensão, para manter um único controlador:

```sh
systemctl --user disable --now power-refresh-rate.service
```

## Testes

Os testes usam GJS, GLib e um barramento D-Bus privado sem ativação automática
de outros serviços. Não trocam os perfis ou modos da sessão real:

```sh
make test
```

Cobrem os três perfis, validação antes de aplicar, escala de 180%, VRR,
monitores externos, conectores em lease, atualização de preferências,
recuperação de falhas e limpeza ao desativar.

Um teste adicional inicia um GNOME Shell virtual e verifica carregamento,
desativação, reativação e a janela GTK/Adwaita de preferências:

```sh
make test-shell
```

O teste virtual foi preparado para Fedora com GNOME Shell 50, Python/PyGObject
e as bibliotecas do aplicativo Extensões instalados. A integração foi testada
em GNOME Shell 50.5 e Mutter 50.6. A primeira versão foi validada com serviços
simulados e uma sessão virtual; a validação no painel físico continua pendente.

## Implantar os perfis de energia em outro Fedora

O [guia de implantação](docs/fedora-energy/README.md) documenta a configuração
complementar de PPD, Intel LPMD, EPP dinâmico por perfil e alimentação, limites
de CPU e política da GPU Intel Xe. Inclui adaptação ao hardware, validação e
reversão. As tabelas são personalizadas e não representam recomendações
universais para qualquer processador.

Os scripts, unidades systemd, testes e a extensão estão no
[pacote de implantação](docs/implementacao-fedora.tar.gz), também disponível
como arquivos em `docs/fedora-energy/`. Esses serviços são opcionais e
instalados separadamente; instalar a extensão não instala os controladores
nem altera os perfis do sistema.

## Desenvolvimento

- `extension.js`: ciclo de vida da extensão.
- `controller.js`: sinais D-Bus, reconciliação e cancelamento.
- `policy.js`: seleção de modos e preservação do layout.
- `prefs.js`: frequências por perfil e diagnóstico.
- `schemas/`: configurações GSettings.
- `tests/`: política, controlador e integração em sessão virtual.
