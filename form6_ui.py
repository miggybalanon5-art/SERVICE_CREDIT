def inject_app_css():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

        html, body, [class*="css"] {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif !important;
        }
        
        .block-container { 
            padding-top: 2rem; 
            padding-bottom: 2rem; 
            max-width: 1200px;
        }
        
        h1, h2, h3, h4, h5, h6 { font-weight: 600; }
        
        .stButton > button {
            background-color: var(--primary-color, #0064E0);
            color: #FFFFFF;
            border: none;
            border-radius: 6px;
            padding: 0.5rem 1rem;
            font-weight: 600;
            box-shadow: none;
            transition: opacity 0.2s;
            min-height: 44px;
        }
        .stButton > button:hover { opacity: 0.9; color: #FFFFFF; }
        .stButton > button:active { opacity: 0.8; color: #FFFFFF; }
        
        .stDownloadButton > button {
            background-color: var(--secondary-background-color);
            color: var(--text-color);
            border: none;
            border-radius: 6px;
            font-weight: 600;
            min-height: 44px;
        }
        .stDownloadButton > button:hover { opacity: 0.8; color: var(--text-color); }
        
        div[data-testid="stMetric"] {
            background-color: var(--background-color);
            border: 1px solid var(--secondary-background-color);
            border-radius: 8px;
            padding: 1.2rem;
            box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
        }
        div[data-testid="stMetricValue"] {
            color: var(--primary-color, #0064E0);
            font-size: 2.2rem;
            font-weight: 700;
        }
        div[data-testid="stMetricLabel"] { font-size: 0.95rem; font-weight: 600; }
        
        /* 1. METRICS: FORCE HORIZONTAL SINGLE LINE FOR BOTH DESKTOP & MOBILE */
        div[data-testid="stHorizontalBlock"]:has(div[data-testid="stMetric"]) {
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            gap: 10px !important;
        }
        div[data-testid="stHorizontalBlock"]:has(div[data-testid="stMetric"]) > div[data-testid="column"] {
            width: auto !important;
            flex: 1 1 0px !important; 
        }

        /* 2. CALENDAR: FACEBOOK COVER PHOTO ASPECT RATIO (820x312) */
        /* This targets common calendar components and iframes automatically */
        iframe[title*="calendar" i],
        .fb-cover-calendar iframe,
        div[data-testid="stHtml"]:has(.fb-cover-calendar) {
            width: 100% !important;
            aspect-ratio: 820 / 312 !important;
            height: auto !important;
            object-fit: cover;
            border: 1px solid var(--secondary-background-color);
            border-radius: 8px;
        }
        
        .streamlit-expanderHeader { font-weight: 600; background-color: transparent; border-radius: 8px; }
        [data-testid="stSidebar"] { border-right: 1px solid var(--secondary-background-color); }
        
        .stTabs [data-baseweb="tab-list"] {
            gap: 20px;
            border-bottom: 1px solid var(--secondary-background-color);
            overflow-x: auto;
            overflow-y: hidden;
            flex-wrap: nowrap !important;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: thin;
        }
        .stTabs [data-baseweb="tab"] {
            height: 50px;
            white-space: nowrap;
            background-color: transparent;
            border-radius: 0;
            font-weight: 600;
            flex-shrink: 0;
        }
        .stTabs [aria-selected="true"] { color: var(--primary-color, #0064E0) !important; border-bottom: 3px solid var(--primary-color, #0064E0) !important; }
        
        div[role="radiogroup"] { flex-direction: row; flex-wrap: wrap; gap: 12px; padding-bottom: 15px; margin-bottom: 20px; }
        div[role="radiogroup"] label { background-color: var(--background-color); border: 1px solid var(--secondary-background-color); border-radius: 20px; padding: 8px 18px; font-weight: 600; }
        div[role="radiogroup"] label[data-checked="true"] { background-color: var(--secondary-background-color); border-color: var(--primary-color, #0064E0); color: var(--primary-color, #0064E0); }
        
        [data-testid="stForm"] { background-color: var(--background-color); padding: 1.5rem; border-radius: 8px; border: 1px solid var(--secondary-background-color); box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05); }
        [data-testid="stDataFrame"] { background-color: var(--background-color); border: 1px solid var(--secondary-background-color); border-radius: 8px; padding: 10px; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05); overflow-x: auto; }
        
        div[data-testid="stHorizontalBlock"] { padding: 6px 10px; border-radius: 6px; transition: background-color 0.15s ease-in-out; }
        div[data-testid="stHorizontalBlock"]:hover { background-color: var(--secondary-background-color) !important; }
        
        .small-note { font-size: 0.95rem; margin-bottom: 1rem; opacity: 0.8; }
        .section-divider { margin: 1.5rem 0; border-top: 1px solid var(--secondary-background-color); }

        div[data-baseweb="input"] > div,
        div[data-baseweb="textarea"] > div,
        div[data-baseweb="select"] > div,
        .stTextInput input,
        .stTextArea textarea,
        .stNumberInput input,
        .stDateInput input {
            border: 1px solid color-mix(in srgb, var(--text-color) 16%, transparent) !important;
            border-radius: 6px !important;
            transition: border-color 0.15s ease-in-out;
        }
        div[data-baseweb="input"] > div:focus-within,
        div[data-baseweb="textarea"] > div:focus-within,
        div[data-baseweb="select"] > div:focus-within {
            border-color: var(--primary-color, #0064E0) !important;
        }

        @media (max-width: 768px) {
            .block-container {
                padding-top: 1rem;
                padding-bottom: 1rem;
                max-width: 100%;
            }

            h1 { font-size: 1.6rem; }
            h2 { font-size: 1.35rem; }
            h3 { font-size: 1.15rem; }

            .stButton > button,
            .stDownloadButton > button {
                min-height: 48px;
                font-size: 1rem;
                padding: 0.75rem 1.25rem;
            }

            /* Stack standard columns on mobile */
            div[data-testid="stHorizontalBlock"] {
                flex-direction: column !important;
                width: 100% !important;
            }
            div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
                width: 100% !important;
            }

            /* Leave metric columns completely alone here! 
               They are forced to be horizontal by the global rule above. */

            div[data-testid="stMetric"] { padding: 0.85rem; }
            div[data-testid="stMetricValue"] { font-size: 1.6rem; }
            div[data-testid="stMetricLabel"] { font-size: 0.85rem; }

            .stTabs [data-baseweb="tab-list"] { gap: 10px; }
            .stTabs [data-baseweb="tab"] {
                height: 42px;
                font-size: 0.85rem;
                padding: 0 4px;
            }

            .streamlit-expanderHeader {
                padding: 1rem;
                font-size: 1.05rem;
            }

            [data-testid="stDataFrame"] { padding: 4px; }
        }

        @media (max-width: 480px) {
            h1 { font-size: 1.4rem; }
            h2 { font-size: 1.2rem; }

            .stButton > button,
            .stDownloadButton > button {
                min-height: 44px;
                font-size: 0.95rem;
                padding: 0.65rem 1rem;
            }

            div[data-testid="stMetricValue"] { font-size: 1.4rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
