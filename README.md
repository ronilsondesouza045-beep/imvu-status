# IMVU Status

Consulta de nick do IMVU — versão **terminal** (CLI) e **app Android** (KivyMD) com interface Material.

Usa os endpoints públicos do próprio site do IMVU (`api.imvu.com`) e o diretório público de salas.
Não faz varredura de pessoas nem fura localização desativada: mostra apenas o que o IMVU expõe publicamente.

## O que mostra

- Online / offline e disponibilidade (quando exposta)
- Sala atual, com indicação de **pública** ou **privada** (quando o IMVU expõe)
- Detecção de possível modo "aparecer offline" (o perfil ainda aponta a última sala)
- Diretório público de salas + dono, lotação e participantes de cada sala
- **Card do avatar**: imagem do avatar (renderização oficial ou foto de perfil), seguidores e o look atual
- Monitoramento a cada X segundos
- Login opcional na sua conta (aumenta o que aparece)
- **Aba APP**: verifica se há versão nova, mostra o que mudou e botão para baixar o APK

## Estrutura

```
main.py                  -> app Android (KivyMD)
buildozer.spec           -> configuração do APK
.github/workflows/       -> GitHub Actions que compila o APK de graça
cli/imvu_status.py       -> versão terminal (menu numerado, Python puro)
assets/                  -> ícone e presplash do app
```

## Como usar a versão terminal

```
python cli/imvu_status.py
```

## Como pegar o APK

**Versões publicadas (recomendado):** aba **Releases** do repositório — o APK fica anexado na versão mais recente.

**Builds de desenvolvimento:** o APK é compilado automaticamente pelo GitHub Actions a cada push:

1. Abra a aba **Actions** do repositório
2. Entre no workflow **build-apk** → último run
3. Baixe o artifact **imvu-status-apk** (contém o `.apk` de debug)
4. No celular, instale com "instalar de fontes desconhecidas" habilitado

O primeiro build demora ~20 a 40 minutos (o Android SDK/NDK é baixado do zero).

Quer compilar local? Precisa de Docker:

```
docker run --rm -v "$PWD":/home/user/hostcwd kivy/buildozer:latest android debug
```

## Como publicar uma atualização (dono do repositorio)

1. Edite o app normalmente (correcoes/melhorias)
2. Atualize a versao nos dois lugares (precisam ser iguais):
   - `main.py` → `APP_VERSAO = "x.y.z"`
   - `buildozer.spec` → `version = x.y.z`
3. Commit e push na `main` (isso valida o build no workflow build-apk)
4. Crie a tag anotada com o changelog (o texto vira as "notas" que o app mostra):
   ```
   git tag -a v1.2.0 -m "Corrigido: ..." -m "Novo: ..."
   git push origin v1.2.0
   ```
5. O workflow **release-apk** compila e cria a Release com o APK anexado
6. Quem tem o app instalado ve a atualizacao na aba **APP** (verifica sozinho ao abrir, ou no botao) com o changelog e o botao de download

## Limitações (importante)

- API **não oficial**: o IMVU pode mudar ou bloquear a qualquer momento; evite excesso de requisições.
- **Localização desligada continua desligada**: se a pessoa desativou a sala, ela não aparece em lugar nenhum — e este projeto não tenta contornar isso.
- Uso por sua conta e risco (ToS do IMVU).
