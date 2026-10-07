# NPTU eeClass 影片下載器

Windows 圖形介面工具，將你已可觀看的國立屏東大學 eeClass 影片下載為 MP4。貼上影片頁面網址後，由專用 Firefox 讀取播放器資料並提供畫質選擇。

目前版本：**1.4，自動瀏覽器版**。本專案提供影片下載，尚未提供格式轉換或重新編碼。

## 使用方式

1. 安裝 Python 3.10 以上（包含 Tcl/Tk、pip、venv）及 Firefox。
2. 安裝 yt-dlp，或保留原先可用的 yt-dlp 安裝。使用相同 Python 環境安裝的指令為：

   ```powershell
   py -3 -m pip install -r requirements.txt
   ```

3. 雙擊 `開啟_eeClass影片下載器.vbs`。若系統停用 VBScript，可雙擊 `啟動_eeClass影片下載器.bat`，或執行 `py -3 eeclass_gui.pyw`。
4. 貼上 eeClass 影片頁面網址，按「解析影片」。
5. 第一次請在程式開啟的專用 Firefox 視窗登入。若登入後仍未顯示畫質，回到下載器按「登入完成，繼續」。
6. 確認畫質、檔名與儲存位置後，按「下載 MP4」。

本版需要 Python，尚未提供獨立 EXE。專用 Firefox 與日常使用的 Firefox 使用不同設定檔，第一次需要另外登入。登入是否能保留至下次使用，依網站的登入期限與 Cookie 規則而定。

## 自動化元件與資料位置

首次解析會在 `%LOCALAPPDATA%\NPTUeeClassDownloader` 建立獨立 Python 環境，安裝固定版本 `selenium==4.50.0`；Selenium Manager 負責取得 geckodriver。首次使用需要可連線至 PyPI 及驅動程式下載來源。

同一目錄下的 `firefox-profile` 保存專用瀏覽器的設定與網站登入狀態。程式不代填密碼，也不將 Cookie 值寫入診斷紀錄。下載時會暫時提供目前 eeClass Cookie 給 yt-dlp，完成或出錯後清除暫存檔；若程序遭作業系統強制終止，暫存檔可能來不及清除。

## 備用方式與限制

- 「匯入 HTML（備用）」保留已成功使用的手動流程，完整操作見 [使用說明](使用說明.txt)。
- 僅支援 NPTU eeClass HTTPS 上、帳號已可觀看的普通 MP4；不處理 DRM、批次下載或影片重新編碼。
- 等待登入期間可取消解析；首次套件安裝與 MP4 下載期間尚無取消按鈕。
- 關閉下載器時會結束它開啟的專用 Firefox；目前操作需先完成或取消。

## 開發與驗證

主程式為 `eeclass_video_downloader.py`，`eeclass_gui.pyw` 提供無主控台啟動與啟動錯誤提示。介面使用 Python 內建的 Tk/ttk。`requirements.txt` 列出下載器相依套件；Selenium 由程式自動安裝到獨立環境，無須額外安裝到主環境。

執行離線測試（需要 Python 的 Tk 模組，但不會開啟 GUI）：

```powershell
py -3 -m unittest discover -s tests -p "test_eeclass*.py"
```

其他系統可使用 `python` 取代 `py -3` 執行測試。測試涵蓋解析、TLS 相容處理、Cookie、模擬登入與取消、頁面範圍檢查，以及下載完成／失敗後的暫存清理。測試使用合成資料與瀏覽器替身，不需要學校帳號。

目前 49 項離線測試通過。使用者已確認 1.3 的手動 HTML 匯入能成功下載；**1.4 的 Windows GUI、Selenium／Firefox 啟動及真實 eeClass 登入下載仍待實機驗證**。

回報問題時請附版本、Windows／Python／Firefox 版本與「複製紀錄」內容。請勿上傳登入 Cookie、瀏覽器設定檔或含私人資料的完整 HTML。

## 技術參考

- [Selenium Manager](https://www.selenium.dev/documentation/selenium_manager/)
- [geckodriver 設定檔](https://firefox-source-docs.mozilla.org/testing/geckodriver/Profiles.html)
- [Selenium 4.50.0](https://pypi.org/project/selenium/4.50.0/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)

本專案尚未指定開源授權。
