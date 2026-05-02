<p align="center">
  <img src="banner.png" alt="calibre-tw-ebook-metadata">
</p>

# calibre-tw-ebook-metadata

> [English](README-en.md)

Calibre 書籍元資料下載外掛，資料來源為台灣繁體中文電子書網站。

每個外掛透過 Calibre 內建的「下載元資料」功能運作，彼此獨立，可同時啟用或與其他元資料來源並行使用。

## 截圖

### 安裝

| 偏好設定 > 外掛 | 從檔案載入外掛 |
|:---:|:---:|
| ![screenshot-01](screenshot-01.png) | ![screenshot-02](screenshot-02.png) |

### 設定

| 偏好設定 > 元資料下載 | 已啟用的元資料來源 |
|:---:|:---:|
| ![screenshot-03](screenshot-03.png) | ![screenshot-04](screenshot-04.png) |

## 支援來源

| 來源 | 網站 | 元資料欄位 |
|------|------|------------|
| **Readmoo** | [readmoo.com](https://readmoo.com) | 書名、作者、出版社、出版日期、ISBN、標籤、簡介、封面、語言 |
| **HyRead** | [ebook.hyread.com.tw](https://ebook.hyread.com.tw) | 書名、作者、出版社、出版日期、ISBN、標籤、簡介、封面、語言 |
| **Pubu** | [pubu.com.tw](https://www.pubu.com.tw) | 書名、作者、出版社、出版日期、ISBN、標籤、簡介、封面、語言、系列 |

## 安裝方式

### 從 GitHub Release 下載（建議）

1. 從[最新 Release](https://github.com/chiahsien/calibre-tw-ebook-metadata/releases/latest) 下載 .zip 檔。

2. 透過指令安裝：

   ```sh
   calibre-customize --add-plugin readmoo.zip
   calibre-customize --add-plugin hyread.zip
   calibre-customize --add-plugin pubu.zip
   ```

   或透過 GUI：*偏好設定 > 外掛 > 從檔案載入外掛*。

3. 重新啟動 Calibre。

### 從原始碼安裝（開發用）

1. Clone 並打包：

   ```sh
   git clone https://github.com/chiahsien/calibre-tw-ebook-metadata.git
   cd calibre-tw-ebook-metadata
   make
   ```

2. 從 `dist/` 安裝：

   ```sh
   calibre-customize --add-plugin dist/readmoo.zip
   calibre-customize --add-plugin dist/hyread.zip
   calibre-customize --add-plugin dist/pubu.zip
   ```

## 使用方式

1. 在 Calibre 中選取一本或多本書。
2. 右鍵 > *下載元資料與封面*。
3. 外掛會以 **Readmoo Books**、**HyRead Books**、**Pubu Books** 出現在來源清單中。
4. Calibre 會整合所有啟用來源的結果，讓你選擇最佳配對。

### 搜尋行為

- **書名 + 作者** 是三個外掛的主要搜尋策略。
- **ISBN** 用於結果過濾，而非搜尋關鍵字（HyRead 除外，有專用 ISBN 搜尋欄位）。
- 提供 ISBN 但無精確比對時，仍會回傳結果作為備選，不會直接捨棄。

## 系統需求

- Calibre >= 5.0.0
- 相容 Python 2/3（使用 Calibre 內建 Python）

## 授權條款

GPL v3

<a href="https://www.buymeacoffee.com/chiahsien" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" style="height: 60px !important;width: 217px !important;" ></a>
