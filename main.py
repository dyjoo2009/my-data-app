"""
KOBIS(영화진흥위원회) 어제 박스오피스 조회 앱
-----------------------------------------------
- 스트림릿 클라우드 배포를 전제로 만들었습니다.
- 인증키는 코드에 쓰지 않고, 스트림릿의 secrets(비밀 금고)에서 불러옵니다.
  (배포 시 앱 설정 > Secrets 에 아래처럼 등록하세요)
    KOBIS_KEY = "발급받은_인증키"
"""

import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  # 시간대(타임존) 계산용 (파이썬 기본 내장 모듈)

# -----------------------------
# 기본 설정
# -----------------------------
st.set_page_config(page_title="어제의 박스오피스", page_icon="🎬", layout="wide")

KOBIS_URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json"


def get_yesterday_kst() -> str:
    """
    '어제' 날짜를 한국 시간(KST) 기준으로 계산해서 yyyymmdd 형식 문자열로 돌려줍니다.
    배포 서버의 시계가 한국 시간이 아니어도, 여기서 명시적으로
    Asia/Seoul 시간대를 지정하기 때문에 항상 한국 기준 '어제'가 나옵니다.
    """
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    yesterday_kst = now_kst - timedelta(days=1)
    return yesterday_kst.strftime("%Y%m%d")


@st.cache_data(ttl=3600)  # 한 시간(3600초) 동안은 같은 날짜로 다시 요청해도 캐시된 결과를 재사용합니다.
def fetch_box_office(target_dt: str) -> dict:
    """
    KOBIS 일별 박스오피스 API를 호출합니다.
    성공하면 {"ok": True, "movies": [...]} 형태로,
    실패하면 {"ok": False, "message": "사람이 읽을 수 있는 안내 문구"} 형태로 돌려줍니다.
    """
    # secrets 금고에서 인증키를 불러옵니다. (코드에는 실제 키 값을 절대 적지 않습니다)
    api_key = st.secrets.get("KOBIS_KEY")
    if not api_key:
        return {
            "ok": False,
            "message": "인증키(KOBIS_KEY)가 설정되어 있지 않습니다. "
                        "스트림릿 클라우드의 Settings > Secrets 에 KOBIS_KEY 값을 등록해 주세요.",
        }

    params = {"key": api_key, "targetDt": target_dt}

    # 1) 네트워크/요청 오류 처리
    try:
        response = requests.get(KOBIS_URL, params=params, timeout=10)
        response.raise_for_status()  # 200번대가 아니면 예외를 일으킴
    except requests.exceptions.RequestException as e:
        return {
            "ok": False,
            "message": f"KOBIS 서버에 요청하는 중 문제가 발생했습니다. "
                       f"인터넷 연결이나 API 주소, 잠시 후 재시도 여부를 확인해 주세요. (상세: {e})",
        }

    # 2) 응답이 JSON 형태가 아닌 경우 처리
    try:
        data = response.json()
    except ValueError:
        return {
            "ok": False,
            "message": "서버 응답을 해석할 수 없습니다(JSON 형식이 아님). "
                       "요청 주소나 파라미터가 올바른지 확인해 주세요.",
        }

    # 3) 인증키 오류 등 faultInfo 상자가 오는 경우 처리
    #    (문서에 따르면 인증키가 틀려도 상태코드는 200이라, 반드시 본문을 확인해야 합니다)
    if "faultInfo" in data:
        fault = data["faultInfo"]
        msg = fault.get("message", "알 수 없는 오류")
        return {
            "ok": False,
            "message": f"KOBIS API가 오류를 반환했습니다: {msg}. "
                       f"인증키(KOBIS_KEY)가 정확한지, 사용량 제한을 넘지 않았는지 확인해 주세요.",
        }

    # 4) 정상 구조가 아니거나 영화 목록이 비어 있는 경우 처리
    box_office_result = data.get("boxOfficeResult")
    if not box_office_result:
        return {
            "ok": False,
            "message": "응답에 boxOfficeResult가 없습니다. API 주소나 요청 변수(targetDt 형식 등)를 확인해 주세요.",
        }

    movies = box_office_result.get("dailyBoxOfficeList")
    if not movies:
        return {
            "ok": False,
            "message": "해당 날짜의 박스오피스 데이터가 비어 있습니다. "
                       "보통 아직 해당 날짜의 집계가 KOBIS에 올라오지 않았을 때 발생합니다. "
                       "잠시 후 다시 시도해 주세요.",
        }

    return {"ok": True, "movies": movies}


def to_dataframe(movies: list) -> pd.DataFrame:
    """
    API에서 받은 영화 목록(list of dict)을 표/그래프에 쓰기 좋은 데이터프레임으로 바꿉니다.
    숫자 항목은 전부 문자열로 오기 때문에 숫자형으로 변환해 줍니다.
    """
    df = pd.DataFrame(movies)

    # 표시에 쓸 컬럼들을 사람이 읽기 좋은 한국어 이름으로 정리합니다.
    df = df.rename(
        columns={
            "rank": "순위",
            "movieNm": "영화명",
            "openDt": "개봉일",
            "audiCnt": "관객수",
            "audiAcc": "누적관객",
            "scrnCnt": "스크린수",
        }
    )

    # 숫자로 와야 하는 컬럼들을 문자열 -> 숫자로 변환합니다.
    # (변환 실패 시 NaN으로 처리해서 앱이 죽지 않도록 합니다)
    number_cols = ["순위", "관객수", "누적관객", "스크린수"]
    for col in number_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 순위 기준으로 정렬합니다.
    df = df.sort_values("순위").reset_index(drop=True)

    return df


def main():
    st.title("🎬 어제의 박스오피스")

    target_dt = get_yesterday_kst()
    pretty_date = f"{target_dt[:4]}년 {target_dt[4:6]}월 {target_dt[6:]}일"
    st.caption(f"기준일(한국시간, 어제): {pretty_date}")

    result = fetch_box_office(target_dt)

    # 실패 시: 빈 화면 대신 무엇을 확인해야 하는지 안내
    if not result["ok"]:
        st.error(result["message"])
        return

    df = to_dataframe(result["movies"])

    # -----------------------------
    # 1위 영화 지표 카드 3장
    # -----------------------------
    top1 = df.iloc[0]
    st.subheader(f"🥇 1위: {top1['영화명']}")

    col1, col2, col3 = st.columns(3)
    col1.metric("오늘 관객수", f"{int(top1['관객수']):,} 명")
    col2.metric("누적 관객수", f"{int(top1['누적관객']):,} 명")
    col3.metric("스크린수", f"{int(top1['스크린수']):,} 개")

    st.divider()

    # -----------------------------
    # 관객수 상위 5편 막대그래프
    # -----------------------------
    st.subheader("📊 관객수 상위 5편")
    top5 = df.sort_values("관객수", ascending=False).head(5)
    chart_data = top5.set_index("영화명")["관객수"]
    st.bar_chart(chart_data)

    st.divider()

    # -----------------------------
    # 전체 표
    # -----------------------------
    st.subheader("📋 전체 박스오피스 순위")
    show_cols = ["순위", "영화명", "개봉일", "관객수", "누적관객", "스크린수"]
    st.dataframe(
        df[show_cols],
        use_container_width=True,
        hide_index=True,
    )


if __name__ == "__main__":
    main()
