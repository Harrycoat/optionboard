# 옵션보드 (OptionBoard)

Max Pain + GEX(Gamma Exposure) 자동 계산 대시보드.
- 검색창에 티커 입력 → 실시간 계산
- 관심종목(watchlist.txt)은 매일 장마감 후 자동 갱신
- 데이터 소스: Massive.com(구 Polygon.io) Options Starter 플랜 — 15분 지연, 계약별 실측 Greeks/IV/OI 직접 제공

## 0. Massive.com API 키 설정 (필수)

이 프로젝트는 `MASSIVE_API_KEY` 환경변수가 있어야 동작합니다.

1. massive.com 대시보드 → `Keys` 메뉴에서 API 키 복사
2. **Vercel**: 프로젝트 → Settings → Environment Variables → `MASSIVE_API_KEY` 등록 (웹사이트 검색 API용)
3. **GitHub**: 저장소 → Settings → Secrets and variables → Actions → `MASSIVE_API_KEY` 등록 (매일 자동 갱신용)

두 군데 다 등록해야 합니다 (Vercel은 실시간 검색용, GitHub Secrets는 daily_update.py 자동 실행용).

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
export MASSIVE_API_KEY="여기에_본인_키"
python api/options_engine.py AAPL      # 콘솔에 결과 출력되는지 확인
python scripts/daily_update.py         # public/watchlist_report.json 채워짐
```
> 이 프로젝트를 만든 샌드박스 환경은 massive.com 도메인이 네트워크 차단되어 있어서, 이 API 연동 코드는 문법 검사와 (키 없을 때) 에러 처리만 확인했고 실제 API 응답으로 검증은 못했습니다. 해리님 로컬 PC나 Vercel 배포 환경에서 첫 실행 시 응답 필드명이 문서와 다르게 오는 경우가 있을 수 있으니, `python api/options_engine.py AAPL` 결과를 한 번 확인해보고 이상하면 알려주세요.

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

## 5. 앞으로 더 개선하려면

- 지금은 Options Starter($29/월, 15분 지연) 플랜이에요. 트래픽/매출이 늘면 Options Advanced($199/월, 실시간)로 업그레이드하면 검색 결과 지연이 없어지고, GitHub Actions 크론 주기도 더 촘촘하게(예: 30분마다) 돌려서 "준실시간 리포트"로 업그레이드 가능해요.

## 6. 법적 문구 / 라이선스 관련 주의

- 페이지 하단에 이미 "정보 제공 목적, 투자자문 아님" 문구를 넣어뒀어요. 유료 구독 모델로 확장 시 이용약관/면책조항을 좀 더 정식으로 갖추는 걸 권장합니다 (변호사 자문까지는 아니어도 템플릿 수준으로).
- **⚠️ 확인 필요**: 지금 가입한 Massive.com "Individual/Personal" 라이선스가, 이 데이터를 가공해서 회원제 웹사이트로 재배포(불특정 다수 회원에게 노출)하는 용도까지 허용하는지 아직 확인 전이에요. Massive 측에 "회원제 웹사이트에서 여러 사용자에게 데이터를 보여줄 예정인데, Individual 플랜으로 가능한지, Business/재배포 라이선스가 별도로 필요한지" 반드시 문의해서 답변 받은 뒤 정식 서비스를 오픈하세요. 확인 전까지는 베타/비공개 테스트 용도로만 쓰는 걸 권장합니다.
- Barchart Premier 데이터는 개인 라이선스라 이 사이트에 직접 가져다 쓰면 안 됩니다.
