# Deploying SmartAttendance on PythonAnywhere (free account)

These steps put the app at `https://YOURNAME.pythonanywhere.com`, where YOURNAME is your PythonAnywhere username.
Replace YOURNAME everywhere below. Plan on about 30 minutes the first time.

## 0. Before you start

* Create a free (Beginner) account at <https://www.pythonanywhere.com/>. Your username becomes your site address,
  so choose it carefully (for example `zdspgcattendance`).
* Free-account limits: one web app with one web worker, 512 MiB disk, no direct support (forums only), and
  **the web app stops after 1 month unless you renew it** (see step 10).

## 1. Make the upload zip (on your PC)

```powershell
cd D:\AngularProjects\SmartAttendance
.venv\Scripts\activate
python deploy\make_upload_zip.py
```

This writes `D:\AngularProjects\SmartAttendance-upload.zip`. It leaves out `.venv`, your local `db.sqlite3`, `.env`,
caches and the `docs` folder (manuscript and ERD, not needed on the server). To start the live site with your local
data, use `python deploy\make_upload_zip.py --include-db`.

Before zipping, run the tests on your PC with `python manage.py test` (all should pass). Don't run them on
PythonAnywhere: they take about a minute of CPU time, and a free account's consoles only get 100 CPU-seconds a day.

## 2. Upload and unzip

1. On PythonAnywhere, open the **Files** tab. You are in `/home/YOURNAME/`. Click **Upload a file** and pick the zip.
2. Open the **Consoles** tab and start a **Bash** console. Run:

```bash
unzip SmartAttendance-upload.zip
cd ~/SmartAttendance
```

## 3. Create the virtualenv and install packages

Check which Python versions are installed with `ls /usr/bin/python3.*`, then use the newest one (3.12 or 3.13;
the app works on 3.10 and newer). The examples use 3.13:

```bash
mkvirtualenv --python=/usr/bin/python3.13 smartattendance
pip install -r requirements.txt
```

The virtualenv activates automatically. In a new console later, run `workon smartattendance` first.

## 4. Create the `.env` file (your secrets)

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(50))"   # run twice, one value for each secret
nano .env
```

Fill in the following, then save (Ctrl+O, Enter) and exit (Ctrl+X):

```
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<first random value>
QR_JWT_SECRET=<second random value>
DJANGO_ALLOWED_HOSTS=YOURNAME.pythonanywhere.com
```

If any of these is missing, the site refuses to start and shows a clear message in the error log, so a forgotten
setting can't slip through.

## 5. Database, static files and accounts

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check --deploy          # should say "no issues"
```

Then set up the accounts. Pick one of the following:

* **Demo data** (the admin, instructors, students and subjects from the README):
  `python manage.py seed_data --password "Choose-A-Strong-Demo-Password"`
  Without `--password`, a random password is generated and printed once at the end, so copy it.
* **Your own Superadmin**: `python manage.py createsuperuser`
  The account automatically gets the Superadmin role and Active status.

## 6. Create the web app

1. Open the **Web** tab and click **Add a new web app**, then **Next**.
2. Choose **Manual configuration**, not the "Django" option, because the Django option creates a new empty project.
3. Choose the **same Python version** as in step 3, then click **Next**.

On the web app's page, set:

| Section | Setting |
|---|---|
| Code → Source code | `/home/YOURNAME/SmartAttendance` |
| Code → Working directory | `/home/YOURNAME/SmartAttendance` |
| Virtualenv | `/home/YOURNAME/.virtualenvs/smartattendance` |
| Static files: URL `/static/` | Directory `/home/YOURNAME/SmartAttendance/staticfiles` |
| Security → Force HTTPS | **Enabled** |

Click the **WSGI configuration file** link (`/var/www/YOURNAME_pythonanywhere_com_wsgi.py`) and delete everything
in it. Paste in the contents of `deploy/pythonanywhere_wsgi.py`, change `YOURNAME`, and click **Save**.

## 7. Reload and test

1. Click the big green **Reload** button on the Web tab.
2. Open `https://YOURNAME.pythonanywhere.com/`. You should see the landing page with the school logo.
3. Sign in as the Superadmin. Then sign in as an instructor on a laptop, start a session, and scan with a phone.

If something fails, check the **Error log** link on the Web tab first.

| Symptom | Fix |
|---|---|
| "Something went wrong" or a 500 page | Read the error log. `ImproperlyConfigured` means `.env` is missing or incomplete. |
| 400 Bad Request | `DJANGO_ALLOWED_HOSTS` in `.env` doesn't match the address you opened. |
| Page has no styling, logo or images | Check the static files mapping, then re-run `collectstatic`. |
| 403 CSRF error when signing in | Open the site with `https://`, and make sure Force HTTPS is on. |
| "Unhandled Exception" right after a change | You forgot to click **Reload**. |

## 8. Updating the site later

Upload the changed files on the Files tab, or upload a new zip and unzip it over the old folder. Then, in a Bash
console:

```bash
cd ~
unzip -o SmartAttendance-upload.zip      # -o replaces the old files without asking (only if you uploaded a zip)
workon smartattendance
cd ~/SmartAttendance
pip install -r requirements.txt          # installs any new package (e.g. openpyxl for the Excel exports)
python manage.py migrate                 # applies new database changes, e.g. the one-phone-per-student rule
python manage.py collectstatic --noinput
```

Finally, click **Reload** on the Web tab. Re-uploading never touches `db.sqlite3` or `.env` unless you upload
those files yourself.

## 9. The command-line menu on PythonAnywhere

The text menu and the benchmark run in a Bash console on the server, against the live database:

```bash
workon smartattendance
cd ~/SmartAttendance
python manage.py attendance_cli                      # 0 to go back / exit
python manage.py dsa_benchmark --sizes 1000,10000    # smaller sizes save CPU-seconds
```

Console work counts toward the free account's 100 CPU-seconds a day. Using the menu costs very little; the full
benchmark (up to 100,000 students) uses several seconds, so demonstrate it on your PC if you can.

## 10. Before the live demonstration

* **Renew the web app.** On the Web tab, click **"Run until 1 month from today"** a few days before the demo,
  so the app can't expire on the day.
* **Back up the database.** Download `db.sqlite3` from the Files tab after the demo, or any time you want a copy.
* **Plan for device binding.** Each student's phone is locked to their account on the first scan.
  * The seeded students (`asantos`, `bramirez`, `cmendoza`, `egarcia`) are bound to made-up phones.
    Press **Unbind Device** on them before the demo, or use `dvillanueva`, whose device is unbound.
  * A different browser, an incognito tab, or clearing browser data counts as a *new phone* and gives "Device
    mismatch". The instructor then presses **Unbind Device**. Tell students to use one normal browser tab.
* **Use a real browser.** Students should open the link in Chrome or Safari, not inside Messenger or Facebook,
  because in-app browsers often block the camera.
* **Expect weak GPS indoors.** Indoor GPS is often only accurate to 20–100 m. For an indoor demo, set the radius to
  60–100 m, or turn geofencing off. Phones need Location turned on.
* **Check the internet.** The styling and the QR scanner now come from your own site, not from outside services.
  Only the Inter font is loaded from Google Fonts, and phones fall back to their own font if it is blocked. Still
  test once on the venue's Wi-Fi or mobile data.
* **Use made-up data.** For a public demo, avoid real student data, or get consent first, as the Data Privacy Act
  of 2012 (RA 10173) requires.
* **Set strong passwords.** Change the Superadmin password, or create a new Superadmin and archive the demo one.
