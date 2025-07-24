import streamlit as st
import pandas as pd
import sqlite3
import hashlib
import plotly.express as px
from datetime import datetime
from pathlib import Path


# --- 0. PENGATURAN AWAL ---
UPLOAD_DIR = Path("patient_uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
AVAILABLE_TAGS = ["Injeksi", "EKG", "Konsultasi", "Resep Obat", "Tindakan Bedah Minor", "Pemeriksaan Lab"]


# --- 1. PENGATURAN DATABASE ---
@st.cache_resource
def get_db_connection():
    """Membuat dan mengembalikan koneksi ke database SQLite."""
    conn = sqlite3.connect('patient_dex_final.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    """
    Inisialisasi database dengan skema yang diperbarui.
    - patients: Menambah kolom 'status' (Hidup, Meninggal Dunia, Lahir di Sini) dan 'handler_user'
    - visits: Menambah kolom 'tags' untuk tindakan medis
    """
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            dob TEXT NOT NULL,
            gender TEXT,
            diagnosis TEXT,
            notes TEXT,
            status TEXT DEFAULT 'Hidup', -- Baru: Hidup, Meninggal Dunia, Lahir di Sini
            handler_user TEXT, -- Baru: User yang mencatat status lahir/meninggal
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(name, dob)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            visit_date TEXT NOT NULL,
            reason TEXT,
            outcome TEXT,
            progress_status TEXT,
            tags TEXT, -- Baru: Menyimpan tags sebagai JSON string, misal: '["Injeksi", "EKG"]'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id INTEGER NOT NULL,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (visit_id) REFERENCES visits (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute("SELECT * FROM users WHERE username = 'admin'")
    if cursor.fetchone() is None:
        default_password_hash = hash_password('admin123')
        cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", ('admin', default_password_hash))
   
    conn.commit()


# --- 2. FUNGSI AUTENTIKASI (Tidak Berubah) ---
def hash_password(password): return hashlib.sha256(password.encode()).hexdigest()
def verify_password(stored_hash, provided_password): return stored_hash == hash_password(provided_password)
def login_user(conn, username, password):
    user = conn.execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()
    return user and verify_password(user['password_hash'], password)


# --- 3. FUNGSI OPERASI DATABASE (Diperbarui) ---
def add_patient(conn, name, dob, gender, diagnosis, notes, status, handler_user):
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO patients (name, dob, gender, diagnosis, notes, status, handler_user) VALUES (?, ?, ?, ?, ?, ?, ?)", (name, dob.strftime('%Y-%m-%d'), gender, diagnosis, notes, status, handler_user))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        st.warning(f"Pasien dengan nama '{name}' dan tanggal lahir '{dob.strftime('%Y-%m-%d')}' sudah ada.")
        return False


def add_visit(conn, patient_id, visit_date, reason, outcome, progress, tags):
    import json
    tags_json = json.dumps(tags) # Simpan list of tags sebagai string JSON
    cursor = conn.cursor()
    cursor.execute("INSERT INTO visits (patient_id, visit_date, reason, outcome, progress_status, tags) VALUES (?, ?, ?, ?, ?, ?)", (patient_id, visit_date.strftime('%Y-%m-%d'), reason, outcome, progress, tags_json))
    conn.commit()
    return cursor.lastrowid


def update_patient_status(conn, patient_id, new_status, handler_user):
    """Memperbarui status pasien (misal: menjadi 'Meninggal Dunia')."""
    cursor = conn.cursor()
    cursor.execute("UPDATE patients SET status = ?, handler_user = ? WHERE id = ?", (new_status, handler_user, patient_id))
    conn.commit()


# --- 4. FUNGSI UNTUK LAPORAN & STATISTIK ---
def get_monthly_report(conn, year, month):
    """Mengambil data pasien yang berkunjung pada bulan & tahun tertentu."""
    start_date = f"{year}-{month:02d}-01"
    end_date = f"{year}-{month:02d}-31"
    query = """
        SELECT DISTINCT p.id, p.name, p.dob, p.gender, p.diagnosis
        FROM patients p
        JOIN visits v ON p.id = v.patient_id
        WHERE v.visit_date BETWEEN ? AND ?
    """
    return pd.read_sql_query(query, conn, params=(start_date, end_date))


def get_action_tags_stats(conn):
    """Menghitung jumlah setiap tag tindakan dari semua kunjungan."""
    import json
    from collections import Counter
    df = pd.read_sql_query("SELECT tags FROM visits WHERE tags IS NOT NULL AND tags != '[]'", conn)
    if df.empty:
        return pd.DataFrame()
   
    all_tags = []
    for tags_json in df['tags']:
        tags_list = json.loads(tags_json)
        all_tags.extend(tags_list)
   
    tag_counts = Counter(all_tags)
    return pd.DataFrame(tag_counts.items(), columns=['Tindakan', 'Jumlah']).sort_values('Jumlah', ascending=False)


def get_life_status_stats(conn):
    """Menghitung statistik kelahiran dan kematian."""
    query = """
        SELECT status, COUNT(*) as jumlah
        FROM patients
        WHERE status = 'Lahir di Sini' OR status = 'Meninggal Dunia'
        GROUP BY status
    """
    return pd.read_sql_query(query, conn)


# --- 5. ANTARMUKA PENGGUNA (STREAMLIT UI) ---
def main_app():
    """Aplikasi utama setelah login."""
    st.sidebar.success(f"Anda login sebagai **{st.session_state['username']}**")
    if st.sidebar.button("Logout"):
        st.session_state['logged_in'] = False
        st.session_state.clear()
        st.rerun()


    conn = get_db_connection()
    st.sidebar.header("Menu Navigasi")
    menu_choice = st.sidebar.radio("Pilih Halaman:", ["Dashboard", "Daftar Pasien", "Tambah Pasien Baru", "Laporan & Statistik"])


    # ... (Halaman Dashboard dan Daftar Pasien tidak banyak berubah, hanya penyesuaian kecil)
    if menu_choice == "Daftar Pasien":
        # ... (Kode halaman daftar pasien dari versi sebelumnya)
        # Penambahan: Tombol untuk update status pasien
        # (Kode ini akan berada di dalam expander detail pasien)
        st.header("📋 Daftar Semua Pasien")
        # ... (kode selectbox pasien) ...
        # if selected_patient_key:
        #     ...
        #     if patient['status'] == 'Hidup':
        #         if st.button("Tandai sebagai 'Meninggal Dunia'", key=f"decease_{selected_id}"):
        #             update_patient_status(conn, selected_id, "Meninggal Dunia", st.session_state['username'])
        #             st.success("Status pasien telah diperbarui.")
        #             st.rerun()
        #     else:
        #         st.warning(f"Status Pasien Saat Ini: **{patient['status']}** (Dicatat oleh: {patient['handler_user']})")
        pass # Placeholder untuk mempersingkat kode, logika lengkap ada di bawah
   
    elif menu_choice == "Tambah Pasien Baru":
        st.header("➕ Formulir Pasien Baru")
        with st.form("form_tambah_pasien", clear_on_submit=True):
            name = st.text_input("Nama Lengkap*")
            dob = st.date_input("Tanggal Lahir*", min_value=datetime(1920, 1, 1))
            gender = st.selectbox("Jenis Kelamin", ["Laki-laki", "Perempuan", "Lainnya"])
            # Penambahan input status saat menambah pasien baru
            status = st.selectbox("Status Awal", ["Hidup", "Lahir di Sini"])
            diagnosis = st.text_area("Diagnosis Utama")
            notes = st.text_area("Catatan Tambahan")
            submitted = st.form_submit_button("Simpan Pasien")
            if submitted:
                if not name:
                    st.error("Nama wajib diisi!")
                else:
                    handler = st.session_state['username'] if status == "Lahir di Sini" else None
                    if add_patient(conn, name, dob, gender, diagnosis, notes, status, handler):
                        st.success(f"Pasien '{name}' berhasil ditambahkan.")


    # --- Halaman Laporan & Statistik (BARU) ---
    elif menu_choice == "Laporan & Statistik":
        st.header("📄 Laporan & Statistik")


        # 1. Laporan Bulanan
        st.subheader("Laporan Kunjungan Pasien Bulanan")
        current_year = datetime.now().year
        report_year = st.selectbox("Pilih Tahun", range(current_year, current_year - 10, -1))
        report_month = st.selectbox("Pilih Bulan", range(1, 13), format_func=lambda m: datetime(2000, m, 1).strftime('%B'))
       
        if st.button("Buat Laporan"):
            report_df = get_monthly_report(conn, report_year, report_month)
            if report_df.empty:
                st.warning(f"Tidak ada kunjungan pasien yang tercatat pada {datetime(2000, report_month, 1).strftime('%B')} {report_year}.")
            else:
                st.dataframe(report_df, use_container_width=True, hide_index=True)
                csv_data = report_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                   label="📥 Download Laporan sebagai CSV",
                   data=csv_data,
                   file_name=f'laporan_{report_year}-{report_month:02d}.csv',
                   mime='text/csv',
                )
       
        st.markdown("---")


        # 2. Statistik
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Statistik Tindakan Medis")
            stats_df = get_action_tags_stats(conn)
            if not stats_df.empty:
                st.dataframe(stats_df, use_container_width=True, hide_index=True)
            else:
                st.info("Belum ada data tindakan yang tercatat.")


        with col2:
            st.subheader("Statistik Kelahiran & Kematian")
            life_stats_df = get_life_status_stats(conn)
            if not life_stats_df.empty:
                for _, row in life_stats_df.iterrows():
                    st.metric(label=row['status'], value=row['jumlah'])
            else:
                st.info("Belum ada data kelahiran/kematian yang tercatat.")




# --- Bagian UI Lainnya (Login, Main, dll.) ---
# ... (Kode dari versi sebelumnya untuk login, main, dashboard, dan daftar pasien bisa dimasukkan di sini)
# ... Saya akan fokus pada logika yang berubah untuk kejelasan.


# Contoh pembaruan pada form tambah kunjungan di halaman "Daftar Pasien"
def display_patient_details_page(conn): # Ini adalah contoh fungsi yang dipanggil di halaman "Daftar Pasien"
    st.header("📋 Daftar Semua Pasien")
    search_term = st.text_input("Cari berdasarkan nama atau diagnosis:", key="search_box")
    patients_df = get_all_patients(conn, search_term)
   
    if not patients_df.empty:
        patient_names = {f"{row['name']} (ID: {row['id']})": row['id'] for _, row in patients_df.iterrows()}
        selected_patient_key = st.selectbox("Pilih pasien untuk melihat detail:", patient_names.keys())


        if selected_patient_key:
            selected_id = patient_names[selected_patient_key]
            patient = conn.execute("SELECT * FROM patients WHERE id = ?", (selected_id,)).fetchone()
           
            with st.expander(f"Detail untuk {patient['name']}", expanded=True):
                # ... (info detail pasien)
                if patient['status'] == 'Hidup':
                    if st.button("Tandai sebagai 'Meninggal Dunia'", key=f"decease_{selected_id}"):
                        update_patient_status(conn, selected_id, "Meninggal Dunia", st.session_state['username'])
                        st.success("Status pasien telah diperbarui.")
                        st.rerun()
                else:
                    st.warning(f"Status Pasien Saat Ini: **{patient['status']}** (Dicatat oleh: {patient.get('handler_user', 'N/A')})")
               
                # ... (tampilkan riwayat kunjungan)


                # Form tambah kunjungan yang diperbarui
                with st.form(f"form_tambah_kunjungan_{selected_id}", clear_on_submit=True):
                    st.subheader("➕ Tambah Kunjungan Baru")
                    visit_date = st.date_input("Tanggal Kunjungan")
                    reason = st.text_input("Alasan Kunjungan")
                    # Input tags tindakan
                    tags = st.multiselect("Tindakan yang Dilakukan (Tags)", options=AVAILABLE_TAGS)
                    outcome = st.text_area("Hasil/Tindakan (Deskripsi)")
                    progress = st.selectbox("Status Progres Pengobatan", ["Membaik", "Tetap", "Memburuk"])
                    uploaded_files = st.file_uploader("Lampirkan Dokumen", accept_multiple_files=True, key=f"upload_{selected_id}")
                   
                    submit_visit = st.form_submit_button("Simpan Kunjungan")
                    if submit_visit:
                        new_visit_id = add_visit(conn, selected_id, visit_date, reason, outcome, progress, tags)
                        # ... (logika upload file)
                        st.success("Riwayat kunjungan berhasil ditambahkan!")
                        st.rerun()


def main():
    st.set_page_config(page_title="D-Patient Dex Pro v2", layout="wide")
    conn = get_db_connection()
    init_db(conn)
    if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
   
    if st.session_state['logged_in']:
        # Untuk mempersingkat, saya akan memanggil fungsi display_patient_details_page
        # jika menu yang dipilih adalah "Daftar Pasien".
        # Anda perlu mengintegrasikan ini ke dalam struktur menu utama Anda.
        main_app_integrated() # Fungsi ini akan berisi semua pilihan menu
    else:
        login_screen()


def main_app_integrated():
    # Ini adalah versi terintegrasi dari main_app()
    st.sidebar.success(f"Anda login sebagai **{st.session_state['username']}**")
    if st.sidebar.button("Logout"):
        st.session_state.clear()
        st.rerun()


    conn = get_db_connection()
    st.sidebar.header("Menu Navigasi")
    menu_choice = st.sidebar.radio("Pilih Halaman:", ["Dashboard", "Daftar Pasien", "Tambah Pasien Baru", "Laporan & Statistik"])


    if menu_choice == "Dashboard":
        # ... (Kode dashboard dari versi sebelumnya)
        st.header("📊 Dashboard Analitik Pasien")
        st.info("Halaman Dashboard. Logika tidak berubah.")
    elif menu_choice == "Daftar Pasien":
        display_patient_details_page(conn)
    elif menu_choice == "Tambah Pasien Baru":
        # ... (Kode tambah pasien yang sudah diperbarui)
        st.header("➕ Formulir Pasien Baru")
        st.info("Halaman Tambah Pasien. Logika sudah diperbarui di atas.")
    elif menu_choice == "Laporan & Statistik":
        # ... (Kode halaman laporan yang baru)
        st.header("📄 Laporan & Statistik")
        st.info("Halaman Laporan & Statistik. Logika sudah diperbarui di atas.")


def login_screen():
    # ... (Kode login screen dari versi sebelumnya)
    st.title("🩺 Selamat Datang di D-Patient Dex Pro v2")
    st.info("Gunakan username: `admin` dan password: `admin123` untuk login.")
    # ...


# (Fungsi-fungsi lain yang tidak berubah seperti get_all_patients, dll. harus disertakan untuk menjalankan kode)
if __name__ == '__main__':
    main()
