# EPS Consensus Improvement Ranker

미국 시총 상위 종목의 EPS 컨센서스 추정치 변화를 yfinance/Yahoo Finance에서 가져와 테이블 이미지로 뽑는 파이썬 CLI입니다.

유료 API를 쓰지 않는 운영을 전제로 `yfinance`/Yahoo Finance 데이터를 사용합니다. API 키가 필요 없지만 Yahoo가 호출량을 제한할 수 있으므로, 호출 수를 줄여 시총 상위 15개 중심으로 운영합니다.

## 핵심 판단

- 무료/no-key 방식으로 운영하므로 yfinance를 사용합니다.
- `avg` EPS는 Yahoo의 `current`, `7daysAgo`, `30daysAgo`, `60daysAgo`, `90daysAgo` 값을 그대로 사용합니다.
- 시총 상위 universe는 yfinance Yahoo screener에서 가져옵니다.
- 현재 결과는 API가 한 번에 제공하는 평균 EPS trend 기준이므로 별도 DB를 사용하지 않습니다.
- 최종 결과물은 `output/` 폴더의 PNG 이미지이며, 실행 로그는 `logs/` 폴더에 저장합니다.

참고:

- yfinance Ticker API: https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.html
- yfinance `get_earnings_estimate`: https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.get_earnings_estimate.html
- yfinance `get_eps_trend`: https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.get_eps_trend.html

## 설치

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`.env.sample`을 복사해 `.env`를 만들고 이미지 렌더러 경로를 설정합니다.

```powershell
Copy-Item .env.sample .env
```

이미지 생성에는 `wkhtmltoimage`가 필요합니다. Windows 기준으로 `wkhtmltopdf`를 설치한 뒤 `.env`의 `WKHTMLTOIMAGE_PATH`를 실제 실행 파일 경로로 맞춥니다. Telegram 전송을 쓰려면 `.env`에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`도 설정합니다.

Linux 서버에서 결과 이미지의 한글이 깨지거나 빈칸으로 보이면 한글 폰트가 없는 상태입니다. Ubuntu/Debian 기준으로 아래처럼 설치한 뒤 다시 실행합니다.

```bash
sudo apt-get update
sudo apt-get install -y wkhtmltopdf fonts-nanum fonts-noto-cjk fontconfig
fc-cache -fv
fc-match "NanumGothic:lang=ko"
```

기본 CSS 폰트는 `Malgun Gothic`, `Noto Sans CJK KR`, `Noto Sans KR`, `NanumGothic` 순서로 잡습니다. 서버에서 `wkhtmltoimage`가 여전히 한글 폰트를 못 잡으면 `.env`에 실제 TTF 파일을 직접 지정합니다.

```bash
IMAGE_FONT_FILE=/usr/share/fonts/truetype/nanum/NanumGothic.ttf
```

서버에 다른 한글 폰트를 쓰려면 `.env`에 `IMAGE_FONT_FAMILY`도 설정할 수 있습니다.

## 시총 상위 15개 EPS 변동률

다음 명령은 Yahoo screener에서 미국 상장 시총 상위 15개를 가져오고, 다음 분기(`+1q`) 평균 EPS estimate 기준으로 Yahoo API가 제공하는 `7일`, `30일`, `60일`, `90일` 변동률 테이블 이미지를 `output/`에 생성합니다. 출력 순서는 EPS 개선율 순위가 아니라 시총 순서입니다.

```powershell
python src\main.py --provider yahoo --universe top-market-cap --universe-limit 15 --field avg --output-mode scanner --lookbacks 7,30,60,90 --period +1q --delay 5 --limit 15
```

`avg`의 기간별 비교값은 yfinance `Ticker.get_eps_trend()`가 제공하는 `7daysAgo`, `30daysAgo`, `60daysAgo`, `90daysAgo` 값을 그대로 사용합니다.
이미지 생성 후 Telegram 설정이 있으면 자동으로 이미지를 전송합니다. 전송을 끄려면 `--no-telegram`을 붙입니다.

## 특정 티커 파일로 실행

```powershell
python src\main.py --provider yahoo --symbols AAPL,MSFT,NVDA --field avg --output-mode scanner --lookbacks 7,30,60,90 --delay 5
```

## 일별 자동 실행

Windows 작업 스케줄러에서 매일 장 마감 후 아래 명령을 실행하면 됩니다.

```powershell
cd C:\Users\imgeongi-notebook\python\us-eps-scanner
.\.venv\Scripts\python.exe src\main.py --provider yahoo --universe top-market-cap --universe-limit 15 --field avg --output-mode scanner --lookbacks 7,30,60,90 --period +1q --delay 5 --limit 15
```

권장 설정:

- `--delay 5` 이상으로 시작합니다. 429가 나면 `--delay 10` 이상으로 늘립니다.
- 매일 1회만 실행합니다.
- `output/*.png`는 매일 최신 결과 이미지로 생성됩니다.
- Telegram 설정이 있으면 생성된 PNG를 자동 전송합니다.
- CSV가 필요할 때만 `--output some.csv`를 명시합니다.

## 결과 이미지 컬럼

- `순위`, `종목`
- `7일`, `30일`, `60일`, `90일`: Yahoo API가 제공하는 평균 EPS trend 대비 변화율

`순위`는 EPS 개선율 순위가 아니라 Yahoo screener에서 가져온 시총 순서입니다.

## 운영 메모

yfinance는 무료/no-key지만 Yahoo 공식 유료 API가 아닙니다. Yahoo가 호출을 제한하면 일부 종목이 누락될 수 있고, 이 경우 다음날 재실행하면 누적 데이터가 계속 보강됩니다.
