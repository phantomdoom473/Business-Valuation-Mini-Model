# Company Valuation Explorer

A simple web app: type a company name (like "Tesla"), it finds the ticker automatically,
pulls real financial data, and runs a DCF valuation — no coding required to use it.

## Option A — Run it on your own computer (fastest way to test)

1. Install Python if you don't already have it: https://www.python.org/downloads/
2. Open a terminal in this folder and run:
   ```
   pip install -r requirements.txt
   ```
3. Then run:
   ```
   streamlit run app.py
   ```
4. It will automatically open in your browser at `http://localhost:8501`

## Option B — Put it online for free (a real public website)

1. Create a free GitHub account (if you don't have one): https://github.com
2. Create a new repository and upload these 3 files: `app.py`, `requirements.txt`, `README.md`
3. Go to https://share.streamlit.io and sign in with your GitHub account
4. Click "New app", select your repository, and set the main file as `app.py`
5. Click "Deploy" — within a minute or two you'll get a public URL
   (like `yourname-valuation-app.streamlit.app`) that anyone can visit, on any device,
   with nothing to install.

This is genuinely a live, working website at that point — great to link on a resume,
a university application, or just to show off.
