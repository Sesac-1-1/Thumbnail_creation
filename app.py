"""자원 인식 영상 썸네일 생성기 (Streamlit).

프로젝트 루트에서 실행:  streamlit run app.py

- views/thumbnail.py : 썸네일 생성 페이지 (/)
- views/resource.py  : 자원 모니터링 페이지 (/resource) — 다른 창에 동시에 띄울 수 있다
- pipeline.py        : Streamlit에 의존하지 않는 처리 로직
services/ 아래 모듈은 Streamlit을 모른다.
"""
import streamlit as st

from views import common

st.set_page_config(page_title="영상 썸네일 생성기", page_icon="🎬", layout="wide")
common.server_lifecycle()
page = st.navigation([
    st.Page(common.THUMBNAIL_PAGE, title="썸네일 생성", icon="🎬", default=True),
    st.Page(common.RESOURCE_PAGE, title="자원 모니터링", icon="📊", url_path="resource"),
])
page.run()
