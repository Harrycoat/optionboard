# 옵션보드 (OptionBoard)

Max Pain + GEX(Gamma Exposure) 자동 계산 대시보드.
- 검색창에 티커 입력 → 실시간 계산
- 관심종목(watchlist.txt)은 매일 장마감 후 자동 갱신
- 데이터 소스: Yahoo Finance(무료, 지연 가능) — 나중에 유료 API로 교체 가능한 구조

---

## 1. 프로젝트 구조

```
optionboard/
├── api/
│   ├── options_engine.py   ← Max Pain / GEX 계산 핵심 로직 (데이터소스 교체는 여기만)
│   └── search.py            ← Vercel 서버리스 함수 (실시간 검색 API)
├── public/
│   ├── index.html            ← 프론트엔드 (단일 파일, 빌드 불필요)
│   └── watchlist_report.json ← 매일 자동 갱신되는 관심종목 결과 (최초엔 비어있음)
├── scripts/
│   ├── daily_update.py       ← 관심종목 리포트 생성 스크립트
│   └── watchlist.txt         ← 관심종목 리스트 (여기 종목만 수정하면 됨)
├── .github/workflows/
│   └── daily-update.yml      ← 매일 자동 실행되는 GitHub Actions 크론잡
├── vercel.json
└── requirements.txt
```

## 2. 로컬에서 먼저 테스트 (선택)

```bash
cd optionboard
pip install -r requirements.txt
python api/options_engine.py AAPL      # 콘솔에 결과 출력되는지 확인
python scripts/daily_update.py         # public/watchlist_report.json 채워짐
```
> 지금 이 프로젝트를 만든 샌드박스 환경은 야후 파이낸스 도메인이 네트워크 차단되어 있어서 여기선 직접 검증 못했어요. 해리님 로컬 PC나 아래 배포 환경에서는 정상 작동해야 합니다. 혹시 에러 나면 `pip install --upgrade yfinance` 한 번 해보세요 (야후가 비공식 API라 가끔 스펙이 바뀝니다).

## 3. 배포 (전부 무료 티어로 가능)

### 3-1. GitHub 저장소 생성
1. github.com에서 새 저장소 생성 (public으로 — private repo는 Actions 무료 시간이 제한적)
2. 이 폴더 전체를 push

```bash
git init
git add .
git commit -m "init optionboard"
git branch -M main
git remote add origin https://github.com/{본인아이디}/optionboard.git
git push -u origin main
```

### 3-2. Vercel 배포 (프론트엔드 + 검색 API)
1. vercel.com 가입 → GitHub 계정 연동
2. "Add New Project" → 방금 만든 저장소 선택 → Deploy (설정 그대로 두면 됨, vercel.json이 알아서 처리)
3. 배포 완료되면 `https://{프로젝트명}.vercel.app` 주소로 바로 접속 가능

### 3-3. GitHub Actions 자동 갱신 활성화
1. 저장소의 Settings → Actions → General → "Workflow permissions"에서 **Read and write permissions** 체크 (daily_update가 결과를 커밋하려면 필요)
2. 별도 설정 없이 매 평일 21:15 UTC(미국 정규장 마감 직후)에 자동 실행됨
3. 바로 테스트해보고 싶으면 저장소 Actions 탭 → "Daily Options Report Update" → **Run workflow** 버튼으로 수동 실행 가능
4. 실행되면 `public/watchlist_report.json`이 갱신되고 자동 커밋 → Vercel이 감지해서 자동 재배포

### 3-4. 관심종목 수정
`scripts/watchlist.txt` 파일에 티커만 한 줄씩 추가/삭제하고 커밋하면 다음 자동 실행부터 반영됩니다.

## 4. 애드센스(구글 광고) 붙이기

1. 사이트가 실제 콘텐츠로 어느 정도 채워지고 (관심종목 20개 이상 + 방문자 유입 시작) 애드센스 신청
2. 승인 나면 `public/index.html`의 `<head>` 안에 애드센스에서 주는 스크립트 태그만 붙이면 끝
3. 심사 통과 팁: 기계적으로 숫자만 나열된 페이지보다, 위 note 영역처럼 **해설/맥락 텍스트가 있는 페이지**가 유리함 → 종목별 "왜 이 레벨이 중요한지" 짧은 설명 추가하면 승인 확률 올라갑니다

## 5. 나중에 유료 데이터로 전환하려면

`api/options_engine.py`의 `fetch_option_chain()` 함수만 Polygon.io나 Tradier API 호출로 교체하면 됩니다. Max Pain/GEX 계산 로직(`compute_max_pain`, `compute_gex`)은 그대로 재사용 가능해요. 실시간 API로 바꾸면:
- 검색 결과 지연 없음
- GitHub Actions 크론 주기를 훨씬 촘촘하게(예: 30분마다) 돌려서 "준실시간 리포트"로 업그레이드 가능

## 6. 법적 문구 관련 주의

- 페이지 하단에 이미 "정보 제공 목적, 투자자문 아님" 문구를 넣어뒀어요. 유료 구독 모델로 확장 시 이용약관/면책조항을 좀 더 정식으로 갖추는 걸 권장합니다 (변호사 자문까지는 아니어도 템플릿 수준으로).
- Barchart Premier 데이터는 개인 라이선스라 이 사이트에 직접 가져다 쓰면 안 됩니다 — 지금 구조는 Yahoo(무료) 기반이라 문제 없습니다.
