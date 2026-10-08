# Grabbit — Advanced Video Downloader (v2)

Python (Flask + yt-dlp) দিয়ে বানানো লোকাল video downloader, আধুনিক UI সহ (dark / light)।
YouTube, Facebook, X, Instagram, TikTok, Vimeo, Dailymotion, Reddit সহ yt-dlp-এর সাপোর্ট করা ১০০০+ সাইটের **public** ভিডিও নামানো যায়।

## v2-তে নতুন যা আছে

| Feature | কী করে |
|---|---|
| **Live progress (SSE)** | polling ছাড়াই সাথে সাথে progress, speed, ETA দেখায়। Video+audio আলাদা stream হলেও bar পিছনে যায় না। |
| **Download history (SQLite)** | app বন্ধ করে আবার খুললেও আগের সব download থাকে। |
| **Crash recovery + Resume** | download চলার সময় app/PC বন্ধ হলে job "Interrupted" হয়ে থাকে, **Resume** চাপলে যেখানে থেমেছিল সেখান থেকে চলে। |
| **Retry** | failed / cancelled job এক ক্লিকে আবার চালানো যায়। |
| **Quality + আনুমানিক size** | প্রতিটা quality-র পাশে ~MB/GB দেখায়, তাই ডাউনলোডের আগেই বুঝতে পারো। |
| **Video ক্লিপ trim** | শুধু `0:30` থেকে `2:15` অংশটুকু নামাও (FFmpeg লাগবে)। |
| **Audio format** | MP3, M4A, Opus, FLAC, WAV। |
| **Container** | MP4 (সবচেয়ে compatible) বা MKV (যেকোনো codec)। |
| **Playlist smart handling** | video+playlist link পেলে আগে শুধু video দেখায়, "Load whole playlist" চাপলে পুরোটা। Playlist items range (`1-5, 8`), আলাদা subfolder, আর "আগে নামানো আইটেম skip" (archive)। |
| **Batch mode** | একসাথে ৫০টা link paste করে preset দিয়ে queue-তে দাও। |
| **Metadata / thumbnail embed** | title, description, chapters ও cover art ফাইলের ভেতরে বসায়। |
| **Subtitle** | ভাষা বেছে নেওয়া যায় (`en`, `bn` ইত্যাদি)। |
| **cookies.txt আপলোড** | Instagram ও login-gated সাইটের জন্য। ফাইল যাচাই করে বলে দেয় Instagram login পাওয়া গেছে কিনা। |
| **Settings panel** | download folder, parallel সংখ্যা (১–৮), speed limit, proxy, browser cookies। |
| **yt-dlp update বোতাম** | সাইট কাজ না করলে UI থেকেই update। |
| **Duplicate সতর্কতা** | একই link একই option-এ আবার দিলে জিজ্ঞেস করে। |
| **Smart error message** | error-এর সাথে কী করতে হবে তার hint দেয়। |
| **Clipboard chip / Drag & Drop / `/` shortcut** | clipboard-এ link থাকলে এক ক্লিকে নেয়, link টেনে এনে ফেলা যায়, `/` চাপলে input-এ focus। |
| **Open folder / ZIP / Delete files** | ফোল্ডার খোলা, playlist ZIP করা, ডিস্ক থেকে ফাইল মোছা। |

## Folder structure

```
video-downloader/
├── app.py              # Flask server + API (SSE stream, batch, settings…)
├── downloader.py       # engine: yt-dlp, queue, SQLite, settings
├── requirements.txt
├── run.bat / run.sh    # launchers
├── templates/index.html
├── static/style.css
├── static/app.js
├── downloads/          # তোমার ফাইল (auto-create, Settings থেকে বদলানো যায়)
└── data/               # history database, settings.json, archive (auto-create)
```

## কীভাবে চালাবে (Windows)

### ১. সাধারণ Python install করো (MSYS2 Python না)
```
winget install Python.Python.3.12
```
নতুন terminal খুলে চেক করো: `py -3.12 --version`

### ২. FFmpeg install করো
HD/4K merge, MP3 convert, trim, embed — সবকিছুর জন্য লাগে।
```
winget install Gyan.FFmpeg
```
এরপর terminal বন্ধ করে নতুন করে খোলো। চেক: `ffmpeg -version`

### ৩. Dependency install ও চালাও
`video-downloader` ফোল্ডারে terminal খুলে (address bar-এ `cmd` লিখে Enter):
```
py -3.12 -m pip install -r requirements.txt
py -3.12 app.py
```
Browser-এ `http://127.0.0.1:5000` খুলবে (না খুললে নিজে যাও)।
`run.bat` চালালেও একই কাজ হয়, তবে Smart App Control `run.bat` আটকালে উপরের command ব্যবহার করো।

### Linux / macOS
```bash
chmod +x run.sh && ./run.sh
```

## ব্যবহার

1. Link paste করে **Analyze** চাপো (বা clipboard chip-এ ক্লিক করো)।
2. Video / Audio, quality, container বেছে নাও। দরকার হলে **Advanced options** থেকে trim, playlist range, subtitle ঠিক করো।
3. **Add to queue** চাপলে নিচে Downloads-এ progress দেখা যাবে।
4. শেষ হলে **Save** (browser দিয়ে) অথবা **Show in folder**। ফাইল আগে থেকেই তোমার download folder-এ জমা হয়ে যায়।

## Instagram ভিডিও নামানোর নিয়ম (গুরুত্বপূর্ণ)

Instagram এখন প্রায় সব Reel/Post দেখতে **login চায়**। তাই cookies ছাড়া সাধারণত নামে না। একবার সেটআপ করলেই হয়:

1. Chrome / Edge / Firefox-এ **"Get cookies.txt LOCALLY"** extension install করো।
2. ওই browser-এ **instagram.com-এ login** করো। সম্ভব হলে একটা **secondary account** ব্যবহার করো (automated access-এ account restrict হতে পারে)।
3. instagram.com-এ থেকেই extension-এ ক্লিক করে **Export** করো (Netscape format)।
4. Grabbit-এ **⚙ Settings → Upload cookies.txt** দিয়ে ফাইলটা দাও। "✓ … Instagram login found" দেখালে ঠিক আছে।
5. এবার Reel/Post-এর link paste করে আগের মতো Analyze → Add to queue।

খেয়াল রাখো:
- **cookies.txt তোমার password-এর মতোই স্পর্শকাতর।** ফাইলটা শুধু তোমার কম্পিউটারের `data` ফোল্ডারে থাকে। কাউকে দিও না, আর কাজ শেষে Settings থেকে **Remove** করো।
- Private account-এর ভিডিও শুধু তখনই নামবে যদি ওই account তুমি follow করো।
- শুধু ছবির post-এ ভিডিও নেই, তাই কিছু নামবে না।
- Chrome/Edge থেকে সরাসরি "Cookies from browser" Windows-এ প্রায়ই কাজ করে না, তাই cookies.txt আপলোড বা Firefox ব্যবহার করো।
- `instagram.com/share/…` লিংক কাজ না করলে লিংকটা browser-এ খুলে address bar-এর শেষ URL টা paste করো।
- তারপরও না হলে Settings → **Update yt-dlp**, app restart, আবার চেষ্টা। Instagram প্রায়ই বদলায়।

## Settings (⚙ আইকন)

| Setting | বিবরণ |
|---|---|
| Download folder | ফাইল কোথায় জমা হবে |
| Parallel downloads | একসাথে কয়টা (১–৮) |
| Speed limit | KB/s, `0` = সীমাহীন |
| Proxy | `http://host:port` বা `socks5://host:port` |
| Cookies from browser / cookies.txt | শুধু **নিজের account-এ দেখা যায়** এমন ভিডিওর জন্য (age-restricted, login লাগে)। Firefox সবচেয়ে নির্ভরযোগ্য, নতুন Chrome/Edge অনেক সময় আটকায়। |
| Embed defaults | metadata / thumbnail / archive-এর ডিফল্ট |

Environment variable (optional): `PORT`, `VD_DOWNLOAD_DIR`, `VD_DATA_DIR`, `VD_MAX_PARALLEL`, `VD_NO_BROWSER=1`।

## সমস্যা হলে (Troubleshooting)

**সবার আগে yt-dlp update করো:** Settings → *Update yt-dlp* (বা `py -3.12 -m pip install -U yt-dlp`)। তারপর app restart।

| সমস্যা | সমাধান |
|---|---|
| `ModuleNotFoundError: No module named 'yt_dlp'` | `py -3.12 -m pip install -r requirements.txt` |
| `pip is not recognized` | সবসময় `py -3.12 -m pip …` লেখো |
| `externally-managed-environment`, path `C:\msys64\…` | তোমার `python` আসলে MSYS2। সাধারণ Python install করো (ধাপ ১) ও `py -3.12` ব্যবহার করো। |
| Smart App Control `run.bat` আটকাচ্ছে | `run.bat` বাদ দিয়ে terminal থেকে চালাও |
| MP3 / trim / HD merge কাজ করছে না | FFmpeg install করে terminal ও app restart দাও |
| `Sign in to confirm you're not a bot` / login লাগে | yt-dlp update; তারপর Settings → Cookies from browser (Firefox) |
| Instagram: `login required` / `empty media response` / `rate-limit` | cookies দরকার। ওপরের "Instagram ভিডিও নামানোর নিয়ম" অনুসরণ করো। |
| `Could not copy Chrome cookie database` / `DPAPI` | Chrome/Edge cookies লক করে রাখে। Settings-এ **cookies.txt আপলোড** করো বা Firefox বেছে নাও। |
| `Private video` / `members only` | এই ভিডিও এই tool-এ নামানো যায় না |
| DRM-protected (Netflix, Disney+…) | সম্ভব না, করাও উচিত না |
| `Port already in use` | `set PORT=5001` দিয়ে `py -3.12 app.py` |
| "Interrupted" দেখাচ্ছে | app বন্ধ ছিল। **Resume** চাপো। |

## Legal / নৈতিক নোট

শুধু নিজের content, public domain, Creative Commons, বা যে ভিডিও save করার অনুমতি আছে সেগুলো নামাও।
বেশিরভাগ সাইটের Terms of Service download নিষিদ্ধ করে, আর copyright আইন দেশভেদে আলাদা। ব্যবহারের দায় ব্যবহারকারীর।
Cookies সুবিধাটি শুধু নিজের account-এর কনটেন্টের জন্য।

## পরবর্তী ধাপের আইডিয়া (শেখার জন্য ভালো exercise)

- Scheduler (রাতে download)
- Electron / Tauri দিয়ে desktop app
- ব্রাউজার extension থেকে এক ক্লিকে "Send to Grabbit"
- Whisper দিয়ে auto-subtitle
