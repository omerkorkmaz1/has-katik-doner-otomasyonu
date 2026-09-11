from flask import Flask, render_template, request, jsonify
import sqlite3
from datetime import datetime
import win32print
import pyperclip
import threading
import time
import os
from PIL import Image

app = Flask(__name__)
DB_NAME = 'durumcu.db'

# --- GLOBAL DURUM DEĞİŞKENLERİ ---
arayan_numara = None
son_gelen_arama = {"telefon": None, "durum": "bekliyor"}

# --- VERİTABANI YARDIMCILARI ---
def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def veritabani_tablo_guncelle():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('ALTER TABLE siparisler ADD COLUMN durum TEXT DEFAULT "Hazırlanıyor"')
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute('ALTER TABLE urunler ADD COLUMN kategori TEXT DEFAULT "durum"')
    except sqlite3.OperationalError:
        pass
        
    try:
        cursor.execute('ALTER TABLE urunler ADD COLUMN sira INTEGER DEFAULT 99')
    except sqlite3.OperationalError:
        pass

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS musteri_adresleri (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telefon TEXT NOT NULL,
            isim TEXT NOT NULL,
            adres TEXT NOT NULL,
            olusturma_tarihi TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute("SELECT COUNT(*) FROM musteri_adresleri")
    if cursor.fetchone()[0] == 0:
        cursor.execute("SELECT telefon, isim, adres FROM musteriler")
        eski_kayitlar = cursor.fetchall()
        for k in eski_kayitlar:
            cursor.execute("INSERT INTO musteri_adresleri (telefon, isim, adres) VALUES (?, ?, ?)", 
                           (k['telefon'], k['isim'], k['adres']))

    conn.commit()
    conn.close()

# --- LOGOYU KARE VE TERTEMİZ BEYAZ ARKA PLANLI FORMATTA ÇEVİRME ---
def logo_hazirla_escpos(genislik=160):
    logo_yolu = os.path.join('static', 'logo.jpeg')
    if not os.path.exists(logo_yolu):
        logo_yolu = os.path.join('static', 'logo.jpg')
    if not os.path.exists(logo_yolu):
        return b""

    try:
        # Resmi açıp kare formatta kırpıyoruz (Siyahlıklar olmadan orijinal kare alan)
        im = Image.open(logo_yolu).convert('RGBA')
        w, h = im.size
        kenar = min(w, h)
        left = (w - kenar) // 2
        top = (h - kenar) // 2
        im_crop = im.crop((left, top, left + kenar, top + kenar))

        # Beyaz arkaplan oluşturuyoruz ki siyah köşeler kesinlikle olmasın
        arkaplan = Image.new('RGBA', (kenar, kenar), (255, 255, 255, 255))
        arkaplan.paste(im_crop, (0, 0), im_crop)

        yukseklik = int((kenar / kenar) * genislik)
        yukseklik = (yukseklik // 8) * 8
        im_resized = arkaplan.resize((genislik, yukseklik), Image.Resampling.LANCZOS)
        
        # Siyah-beyaz termal formatına dönüştür
        im_bw = im_resized.convert('1')

        bw_genislik, bw_yukseklik = im_bw.size
        x_bytes = bw_genislik // 8
        
        xL = x_bytes % 256
        xH = x_bytes // 256
        yL = bw_yukseklik % 256
        yH = bw_yukseklik // 256
        
        komut = b'\x1d\x76\x30\x00' + bytes([xL, xH, yL, yH])
        veri = im_bw.tobytes()
        
        # Ortala (\x1b\x61\x01), logoyu bas, sola hizala ve bir satır boşluk bırak
        return b'\x1b\x61\x01' + komut + veri + b'\x1b\x61\x00\n'
    except Exception as e:
        print(f"Logo çevirme hatası: {e}")
        return b""

def turkce_karakter_duzelt(metin):
    if not metin:
        return ""
    kaynak = "şŞçÇğĞüÜöÖıİ"
    hedef  = "sScCgGuUoOiI"
    tablo = str.maketrans(kaynak, hedef)
    return str(metin).translate(tablo)

def sanal_fis_bas(isim, telefon, adres, icerik_yapisi, toplam_tutar, notlar):
    simdi = datetime.now().strftime('%d.%m.%Y %H:%M')
    
    isim = turkce_karakter_duzelt(isim)
    adres = turkce_karakter_duzelt(adres)
    if notlar:
        notlar = turkce_karakter_duzelt(notlar)
    
    fis = "\n"
    fis += "\x1b\x61\x01"      # Ortala
    fis += "\x1b\x21\x20"      # Büyük Yazı
    fis += "HAS KATIK DONER\n"
    fis += "\x1b\x21\x00"      # Normal Boyut
    fis += "\x1b\x61\x00"      # Sola Hizala
    fis += "========================================\n"
    fis += "              SIPARIS FISI              \n"
    fis += "========================================\n"
    fis += f"Tarih/Saat : {simdi}\n"
    fis += "----------------------------------------\n"
    fis += "            MUSTERI BILGILERI           \n"
    fis += f"Isim  : {isim}\n"
    fis += f"Tel   : {telefon}\n"
    fis += f"Adres : {adres}\n"
    fis += "----------------------------------------\n"
    fis += "              SIPARIS ICERIGI           \n"
    
    # Gruplama mantığı
    gruplanmis = {}
    for item in icerik_yapisi:
        ad = turkce_karakter_duzelt(item.get('urunAd', '').strip())
        adet = item.get('adet', 1)
        modlar = sorted([turkce_karakter_duzelt(m.strip()) for m in item.get('modlar', [])])
        
        mod_key = tuple(modlar)
        anahtar = (ad, mod_key)
        
        if anahtar in gruplanmis:
            gruplanmis[anahtar]['adet'] += adet
        else:
            gruplanmis[anahtar] = {
                'adet': adet,
                'modlar': modlar
            }

    for (ad, mod_key), veri in gruplanmis.items():
        adet = veri['adet']
        modlar = veri['modlar']
        
        fis += f" [{adet}x] {ad}\n"
        if modlar:
            for m in modlar:
                fis += f"      >> {m}\n"
        
    fis += "----------------------------------------\n"
    if notlar and notlar.strip():
        fis += "              SIPARIS NOTU              \n"
        fis += f"* {notlar} *\n"
        fis += "----------------------------------------\n"
    fis += f"TOPLAM TUTAR : {toplam_tutar} TL\n"
    fis += "========================================\n"
    fis += "       BIZI TERCIH ETTIGINIZ ICIN       \n"
    fis += "              TESEKKUR EDERIZ           \n"
    fis += "========================================\n\n\n\n"

    print(fis)

    try:
        yazici_adi = "A10M MiniPrinter"
        h_yazici = win32print.OpenPrinter(yazici_adi)
        try:
            h_is = win32print.StartDocPrinter(h_yazici, 1, ("Has Katik Siparis", None, "RAW"))
            try:
                win32print.StartPagePrinter(h_yazici)
                win32print.WritePrinter(h_yazici, b'\x1b\x40')
                
                # Kare formatlı logo görselini gönder[cite: 6]
                logo_bytes = logo_hazirla_escpos(genislik=160)
                if logo_bytes:
                    win32print.WritePrinter(h_yazici, logo_bytes)
                
                win32print.WritePrinter(h_yazici, fis.encode('cp1254', errors='replace'))
                
                kesme_komutu = b'\x1d\x56\x41\x00' 
                win32print.WritePrinter(h_yazici, kesme_komutu)
                
                win32print.EndPagePrinter(h_yazici)
            finally:
                win32print.EndDocPrinter(h_yazici)
        finally:
            win32print.ClosePrinter(h_yazici)
            
    except Exception as e:
        print(f"\n⚠️ Yazıcıya gönderilirken hata oluştu: {e}\n")

def otomatik_caller_id_dinle():
    global son_gelen_arama
    son_pano = ""
    print("📞 CIDShow Otomatik Arama Dinleyicisi Aktif! Arama bekleniyor...")

    while True:
        try:
            pano_icerigi = pyperclip.paste().strip()
            if pano_icerigi and pano_icerigi != son_pano:
                rakamlar = ''.join(filter(str.isdigit, pano_icerigi))
                if len(rakamlar) >= 10:
                    temiz_no = rakamlar[-10:]
                    print(f"\n🔔 OTOMATİK ARAMA DÜŞTÜ: 0{temiz_no}\n")
                    son_gelen_arama = {"telefon": temiz_no, "durum": "yeni_arama"}
                    son_pano = pano_icerigi
        except Exception:
            pass
        time.sleep(0.3)

threading.Thread(target=otomatik_caller_id_dinle, daemon=True).start()

@app.route('/')
def ana_sayfa():
    return render_template('index.html')

@app.route('/yeni-arama-var-mi')
def yeni_arama_var_mi():
    global son_gelen_arama, arayan_numara
    if son_gelen_arama["durum"] == "yeni_arama":
        temp = son_gelen_arama.copy()
        son_gelen_arama["durum"] = "bekliyor"
        return jsonify(temp)
    elif arayan_numara:
        gecici = arayan_numara
        arayan_numara = None
        return jsonify({'durum': 'yeni_arama', 'telefon': gecici})
    return jsonify({'durum': 'yok'})

@app.route('/menu-getir')
def menu_getir():
    conn = get_db_connection()
    urunler = conn.execute('SELECT * FROM urunler ORDER BY sira ASC, id ASC').fetchall()
    conn.close()
    
    sonuc = []
    for u in urunler:
        d = dict(u)
        ad = d['urun_adi'].strip()
        kat = d.get('kategori')
        if not kat or kat == 'durum':
            if ad.startswith('+'):
                kat = 'ekstra'
            elif ad.startswith('-'):
                kat = 'cikar'
            elif any(x in ad.lower() for x in ['ayran', 'kola', 'fanta', 'su', 'soda', 'fusetea', 'cappy']):
                kat = 'icecek'
            else:
                kat = 'durum'
        d['kategori'] = kat
        sonuc.append(d)
    return jsonify(sonuc)

@app.route('/urun-kaydet', methods=['POST'])
def urun_kaydet():
    data = request.json
    urun_id = data.get('id')
    ad = data.get('urun_adi')
    fiyat = data.get('fiyat')
    kategori = data.get('kategori', 'durum')
    sira = data.get('sira', 99)
    
    conn = get_db_connection()
    if urun_id:
        conn.execute('UPDATE urunler SET urun_adi = ?, fiyat = ?, kategori = ?, sira = ? WHERE id = ?', 
                     (ad, fiyat, kategori, sira, urun_id))
    else:
        conn.execute('INSERT INTO urunler (urun_adi, fiyat, kategori, sira) VALUES (?, ?, ?, ?)', 
                     (ad, fiyat, kategori, sira))
    conn.commit()
    conn.close()
    return jsonify({'durum': 'basarili'})

@app.route('/urun-sil/<int:id>', methods=['DELETE'])
def urun_sil(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM urunler WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return jsonify({'durum': 'basarili'})

@app.route('/musteri/<telefon>')
def musteri_sorgula(telefon):
    conn = get_db_connection()
    kayitlar = conn.execute('''
        SELECT id, isim, adres FROM musteri_adresleri 
        WHERE telefon = ? 
        ORDER BY id DESC
    ''', (telefon,)).fetchall()
    conn.close()
    
    if kayitlar:
        return jsonify({
            'durum': 'bulundu',
            'adresler': [dict(k) for k in kayitlar]
        })
    return jsonify({'durum': 'bulunamadi'})

@app.route('/musteri-adresi-sil/<int:id>', methods=['DELETE'])
def musteri_adresi_sil(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM musteri_adresleri WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return jsonify({'durum': 'basarili'})

@app.route('/siparis-ekle', methods=['POST'])
def siparis_ekle():
    data = request.json
    telefon = data.get('telefon')
    isim = data.get('isim').strip()
    adres = data.get('adres').strip()
    sepet_yapisi = data.get('sepet_yapisi', [])
    toplam_tutar = data.get('toplam_tutar')
    notlar = data.get('notlar')
    
    icerik_metinleri = []
    for item in sepet_yapisi:
        ad = item.get('urunAd')
        adet = item.get('adet', 1)
        modlar = item.get('modlar', [])
        satir = f"[{adet}x] {ad}"
        if modlar:
            satir += f" ({', '.join(modlar)})"
        icerik_metinleri.append(satir)
        
    duz_icerik = " | ".join(icerik_metinleri)
    
    conn = get_db_connection()
    
    var_mi = conn.execute('''
        SELECT id FROM musteri_adresleri 
        WHERE telefon = ? AND LOWER(isim) = LOWER(?) AND LOWER(adres) = LOWER(?)
    ''', (telefon, isim, adres)).fetchone()
    
    if not var_mi:
        conn.execute('INSERT INTO musteri_adresleri (telefon, isim, adres) VALUES (?, ?, ?)', 
                     (telefon, isim, adres))
                     
    conn.execute('INSERT OR REPLACE INTO musteriler (telefon, isim, adres) VALUES (?, ?, ?)',
                 (telefon, isim, adres))
        
    conn.execute('''
        INSERT INTO siparisler (musteri_telefon, icerik, toplam_tutar, notlar, durum) 
        VALUES (?, ?, ?, ?, 'Hazırlanıyor')
    ''', (telefon, duz_icerik, toplam_tutar, notlar))
    conn.commit()
    conn.close()
    
    sanal_fis_bas(isim, telefon, adres, sepet_yapisi, toplam_tutar, notlar)
    return jsonify({'durum': 'basarili'})

@app.route('/aktif-siparisler')
def aktif_siparisler():
    conn = get_db_connection()
    siparisler = conn.execute('''
        SELECT s.id, m.isim, m.telefon, m.adres, s.icerik, s.toplam_tutar, s.notlar, s.durum,
               time(s.tarih, 'localtime') as saat
        FROM siparisler s 
        JOIN musteriler m ON s.musteri_telefon = m.telefon 
        WHERE s.durum NOT IN ('Teslim Edildi', 'İptal Edildi')
        ORDER BY s.id DESC
    ''').fetchall()
    conn.close()
    return jsonify([dict(s) for s in siparisler])

@app.route('/durum-guncelle', methods=['POST'])
def durum_guncelle():
    data = request.json
    siparis_id = data.get('id')
    yeni_durum = data.get('durum')
    conn = get_db_connection()
    conn.execute('UPDATE siparisler SET durum = ? WHERE id = ?', (yeni_durum, siparis_id))
    conn.commit()
    conn.close()
    return jsonify({'durum': 'basarili'})

@app.route('/tumunu-teslim-et', methods=['POST'])
def tumunu_teslim_et():
    conn = get_db_connection()
    conn.execute("UPDATE siparisler SET durum = 'Teslim Edildi' WHERE durum NOT IN ('Teslim Edildi', 'İptal Edildi')")
    conn.commit()
    conn.close()
    return jsonify({'durum': 'basarili'})

@app.route('/gecmis-siparisler', methods=['GET'])
def gecmis_siparisler():
    tarih = request.args.get('tarih')
    arama_no = request.args.get('arama_no', '').strip()
    
    conn = get_db_connection()
    
    if arama_no:
        temiz_arama = f"%{arama_no}%"
        liste = conn.execute('''
            SELECT s.id, m.isim, m.telefon, m.adres, s.icerik, s.toplam_tutar, s.notlar, s.durum,
                   datetime(s.tarih, 'localtime') as saat
            FROM siparisler s
            JOIN musteriler m ON s.musteri_telefon = m.telefon
            WHERE m.telefon LIKE ? OR m.isim LIKE ?
            ORDER BY s.id DESC
        ''', (temiz_arama, temiz_arama)).fetchall()
        
        ozet = conn.execute('''
            SELECT SUM(s.toplam_tutar) as ciro, COUNT(*) as adet
            FROM siparisler s
            JOIN musteriler m ON s.musteri_telefon = m.telefon
            WHERE (m.telefon LIKE ? OR m.isim LIKE ?) AND s.durum = 'Teslim Edildi'
        ''', (temiz_arama, temiz_arama)).fetchone()
        tarih_str = f"Arama: {arama_no}"
    else:
        if not tarih:
            tarih = datetime.now().strftime('%Y-%m-%d')
            
        liste = conn.execute('''
            SELECT s.id, m.isim, m.telefon, m.adres, s.icerik, s.toplam_tutar, s.notlar, s.durum,
                   time(s.tarih, 'localtime') as saat
            FROM siparisler s
            JOIN musteriler m ON s.musteri_telefon = m.telefon
            WHERE date(s.tarih, 'localtime') = ?
            ORDER BY s.id DESC
        ''', (tarih,)).fetchall()
        
        ozet = conn.execute('''
            SELECT SUM(toplam_tutar) as ciro, COUNT(*) as adet
            FROM siparisler
            WHERE date(tarih, 'localtime') = ? AND durum = 'Teslim Edildi'
        ''', (tarih,)).fetchone()
        tarih_str = tarih

    conn.close()
    
    return jsonify({
        'tarih': tarih_str,
        'ciro': ozet['ciro'] if ozet['ciro'] else 0,
        'adet': ozet['adet'] if ozet['adet'] else 0,
        'liste': [dict(x) for x in liste]
    })

@app.route('/gunluk-rapor')
def gunluk_rapor():
    conn = get_db_connection()
    ozet = conn.execute('''
        SELECT SUM(toplam_tutar) as ciro, COUNT(*) as adet 
        FROM siparisler 
        WHERE durum = 'Teslim Edildi' AND date(tarih, 'localtime') = date('now', 'localtime')
    ''').fetchone()
    
    islemler = conn.execute('''
        SELECT s.id, m.isim, s.icerik, s.toplam_tutar, time(s.tarih, 'localtime') as saat
        FROM siparisler s
        JOIN musteriler m ON s.musteri_telefon = m.telefon
        WHERE s.durum = 'Teslim Edildi' AND date(s.tarih, 'localtime') = date('now', 'localtime')
        ORDER BY s.id DESC
    ''').fetchall()
    conn.close()
    
    return jsonify({
        'ciro': ozet['ciro'] if ozet['ciro'] else 0,
        'adet': ozet['adet'] if ozet['adet'] else 0,
        'liste': [dict(i) for i in islemler]
    })

if __name__ == '__main__':
    veritabani_tablo_guncelle()
    app.run(debug=True, host='0.0.0.0', port=5000)
