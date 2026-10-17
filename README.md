# IMVU Status

Consulta de nick do IMVU — versão **terminal** (CLI) e **app Android** (KivyMD) com interface Material.

Usa os endpoints públicos do próprio site do IMVU (`api.imvu.com`) e o diretório público de salas.
Não faz varredura de pessoas nem fura localização desativada: mostra apenas o que o IMVU expõe publicamente.

## O que mostra

- Online / offline e disponibilidade (quando exposta)
- Sala atual, com indicação de **pública** ou **privada** (quando o IMVU expõe)
- Detecção de possível modo "aparecer offline" (o perfil ainda aponta a última sala)
- Diretório público de salas + dono, lotação e participantes de cada sala
- Monitoramento a cada X segundos
- Login opcional na sua conta (aumenta o que aparece)

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

O APK é compilado automaticamente pelo GitHub Actions a cada push:

1. Abra a aba **Actions** do repositório
2. Entre no workflow **build-apk** → último run
3. Baixe o artifact **imvu-status-apk** (contém o `.apk` de debug)
4. No celular, instale com "instalar de fontes desconhecidas" habilitado

O primeiro build demora ~20 a 40 minutos (o Android SDK/NDK é baixado do zero).

Quer compilar local? Precisa de Docker:

```
docker run --rm -v "$PWD":/home/user/hostcwd kivy/buildozer:latest android debug
```

## Limitações (importante)

- API **não oficial**: o IMVU pode mudar ou bloquear a qualquer momento; evite excesso de requisições.
- **Localização desligada continua desligada**: se a pessoa desativou a sala, ela não aparece em lugar nenhum — e este projeto não tenta contornar isso.
- Uso por sua conta e risco (ToS do IMVU).
