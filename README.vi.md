# Hướng dẫn tiếng Việt — khai thuế thu nhập Đức với Claude

Tài liệu này hướng dẫn **cài đặt và sử dụng** `steuer-de`, một plugin Claude giúp làm tờ khai
thuế thu nhập cá nhân ở Đức (*Einkommensteuererklärung*).

> Bản gốc tiếng Đức: [README.md](README.md). Đây **không phải tư vấn thuế** — con số ràng
> buộc là do ELSTER tính, và phần kiểm tra cuối vẫn nên nhờ một *Steuerberater*.

---

## 1. Nó làm được gì

Bạn có một chồng giấy tờ: *Lohnsteuerbescheinigung* của công ty, *Steuerbescheinigung* của
ngân hàng, *Beitragsbescheinigung* của bảo hiểm y tế, và nếu có đầu tư thì thêm report của
sàn chứng khoán hoặc crypto. Cuối cùng vài chục con số phải chui vào đúng ô trong ELSTER —
và phần việc thật nằm ở khoảng giữa.

Plugin làm đúng khoảng giữa đó:

- **tự nhận dạng** từng file là loại giấy tờ gì, không đoán bừa;
- **đọc số tiền ra** từ PDF, kể cả bản scan (qua OCR);
- **tính**: § 32a (biểu thuế), § 35a (thợ thuyền, giúp việc), § 32b (Progressionsvorbehalt),
  § 10 Abs. 3/4 (bảo hiểm hưu trí/y tế), § 33 (chi phí bất thường), § 31 (so sánh
  Kinderfreibetrag với Kindergeld), thuế đoàn kết, thuế nhà thờ, thuế vốn;
- **crypto đúng luật**: FIFO trên toàn bộ lịch sử mua, thời hạn nắm giữ tính theo ngày,
  Freigrenze tính **theo người** chứ không theo từng sàn;
- **xuất** HTML, PDF và **file CSV ánh xạ sang từng dòng ELSTER**, rồi dẫn bạn đi qua form.

Không nộp hộ, không gửi dữ liệu đi đâu. Mọi thứ chạy cục bộ, kết quả là file trên máy bạn.
Hỗ trợ các năm tính thuế **2022–2026**, chỉ luật thuế Đức.

---

## 2. Ba cách dùng — chọn cách hợp với bạn

Đây là chỗ hay nhầm nhất: **các lệnh gạch chéo (`/einstieg`, `/steuererklaerung`) chỉ có
trong Claude Code.** Nếu bạn chỉ dùng app Claude Desktop bình thường thì gõ `/einstieg` sẽ
không ra gì cả — nhưng bạn vẫn dùng được, chỉ là theo cách khác.

| | **A. Claude Code + plugin** | **B. Claude Desktop + Skill** | **C. Chat trần, không cài gì** |
|---|---|---|---|
| Dùng cho ai | ai đã/sẵn sàng dùng Claude Code (terminal hoặc tab Claude Code trong app) | **đa số người dùng Claude Desktop / claude.ai** | dùng thử nhanh |
| Cài đặt | 2 lệnh, xong | tải 1 file ZIP lên phần cài đặt | không cần gì |
| Có lệnh `/` không | **Có** — cả 6 lệnh | Không — bạn nói bằng lời thường | Không |
| Chạy được script tính toán | Có | Có (phải bật code execution) | **Không** |
| Đọc PDF, OCR, FIFO, xuất CSV ELSTER | Có | Có | Không |
| Độ chính xác | đầy đủ | đầy đủ | **chỉ ước lượng thô — đừng nộp** |

**Khuyến nghị:** nếu bạn chỉ dùng Claude Desktop → **cách B**. Nó cho kết quả y hệt cách A,
chỉ khác là bạn nói bằng câu chữ bình thường thay vì gõ lệnh.

---

## 3. Cách A — Cài plugin trong Claude Code

Claude Code có trong terminal, trong **app Claude Desktop (Mac/Windows)**, trên web
(claude.ai/code) và trong VS Code / JetBrains. Mở Claude Code rồi gõ hai lệnh này vào ô chat:

```
/plugin marketplace add truongtud/steuererklaerung-de
/plugin install steuer-de@steuer-de
```

Xong. **Mở một phiên chat mới** — plugin chỉ có hiệu lực ở phiên mới. Gõ `/` sẽ thấy 6 lệnh.
(Muốn hiểu hai lệnh đó làm gì, hoặc cài từ một thư mục trên máy: xem mục 4.)

**Cập nhật khi có bản mới** (lệnh update so sánh *số phiên bản*, không so nội dung, nên phải
làm mới cache trước):

```
/plugin marketplace update steuer-de
/plugin update steuer-de@steuer-de
```

Rồi lại mở phiên mới.

---

## 4. Marketplace là gì, và thêm bằng cách nào

Repo này **chính là một marketplace** — một danh mục plugin, khai báo trong file
`.claude-plugin/marketplace.json`, bên trong hiện có đúng một plugin tên `steuer-de`. Vì
vậy việc cài luôn gồm **hai bước**: thêm marketplace vào Claude, rồi cài plugin từ
marketplace đó.

### Bước 1 — Thêm marketplace

Có ba nguồn, chọn một:

| Nguồn | Lệnh |
|---|---|
| **GitHub** (khuyến nghị) | `/plugin marketplace add truongtud/steuererklaerung-de` |
| URL git đầy đủ | `/plugin marketplace add https://github.com/truongtud/steuererklaerung-de.git` |
| **Thư mục trên máy** (đã clone hoặc giải nén) | `/plugin marketplace add ./đường/dẫn/steuererklaerung-de` |

Đường dẫn local hữu ích khi bạn muốn thử bản sửa của mình trước khi đẩy lên GitHub.

### Bước 2 — Cài plugin từ marketplace

```
/plugin install steuer-de@steuer-de
```

Cú pháp là `plugin@marketplace`. Ở đây hai chữ trùng nhau vì marketplace và plugin cùng
tên — không phải gõ nhầm.

### Quản lý về sau

Trong chat thì gõ `/plugin ...`; ngoài terminal thì `claude plugin ...` — cùng bộ lệnh.

| Việc | Lệnh |
|---|---|
| Xem các marketplace đang có | `/plugin marketplace list` |
| **Làm mới marketplace** (bắt buộc trước khi update) | `/plugin marketplace update steuer-de` |
| Cập nhật plugin lên bản mới | `/plugin update steuer-de@steuer-de` |
| Xem plugin đã cài | `/plugin list` |
| Tắt tạm / bật lại | `/plugin disable steuer-de` · `/plugin enable steuer-de` |
| Gỡ plugin | `/plugin uninstall steuer-de@steuer-de` |
| Gỡ hẳn marketplace | `/plugin marketplace remove steuer-de` |

> **Vì sao phải làm mới trước khi update:** lệnh update so **số phiên bản** chứ không so
> nội dung file. Chưa làm mới thì Claude vẫn thấy số cũ và báo „đã mới nhất". Và mọi thay
> đổi chỉ có hiệu lực ở **phiên chat mới**.

### Ở Claude Desktop thì sao?

Các lệnh trên chỉ chạy trong **Claude Code** — kể cả khi Claude Code nằm trong app Desktop.
**Cửa sổ chat thường của Claude Desktop không thêm được marketplace**, vì nó không có hệ
thống plugin. Ở đó dùng [cách B](#5-cách-b--claude-desktop-không-có-lệnh-gạch-chéo).

Nối repo vào một **Project** cũng không thay thế được: Claude sẽ **đọc** được mã nguồn dưới
dạng văn bản, nhưng các script không nằm trong sandbox chạy code, nên không chạy được. Nối
Project hợp cho việc đọc và sửa mã nguồn, không phải để dùng.

---

## 5. Cách B — Claude Desktop, không có lệnh gạch chéo

Claude Desktop và claude.ai không cài được *plugin*, nhưng cài được **Skill** — cùng một
bộ não, chỉ khác cách gọi. Có trên mọi gói: Free, Pro, Max, Team, Enterprise.

### Bước 1 — Bật code execution

`Settings` → `Capabilities` → bật **code execution** (bản Team/Enterprise thì chủ tổ chức
bật trong `Organization settings` → `Skills`).

**Không bật thì plugin không tính được gì** — nó cần chạy Python để đọc PDF và tính thuế.

### Bước 2 — Tạo file ZIP của skill

Tải repo này về (nút `Code` → `Download ZIP` trên GitHub, rồi giải nén), sau đó đóng gói
**đúng thư mục `steuererklaerung`**:

**macOS / Linux:**

```bash
cd steuererklaerung-de/plugins/steuer-de/skills
zip -r steuererklaerung.zip steuererklaerung \
    -x "steuererklaerung/tests/*" "*__pycache__*"
```

**Windows:** mở thư mục `steuererklaerung-de\plugins\steuer-de\skills`, xoá thư mục con
`steuererklaerung\tests` (không cần thiết), rồi bấm chuột phải vào thư mục
`steuererklaerung` → *Gửi tới* → *Thư mục nén (zipped)*.

> Quan trọng: bên trong file ZIP phải là **thư mục `steuererklaerung/`**, và ngay trong đó
> có file `SKILL.md`. Nén nhầm cấp là Claude sẽ không nhận.

### Bước 3 — Tải lên

`Settings` → `Customize` → `Skills` → nút `+` → `Create skill` → **`Upload a skill`** → chọn
file ZIP vừa tạo → bật skill lên.

### Bước 4 — Dùng bằng lời thường

Không có `/steuererklaerung`. Bạn **đính kèm giấy tờ và nói bằng câu bình thường** — skill tự
kích hoạt khi thấy nhắc tới thuế:

> 📎 *lohnsteuerbescheinigung-2024.pdf, steuerbescheinigung-bank.pdf*
>
> „Làm giúp tôi tờ khai thuế thu nhập Đức năm 2024. Độc thân, thuế nhà thờ 9 %, không con."

Muốn phần chuẩn bị (tương đương `/einstieg`) thì hỏi thẳng:

> „Tôi chưa biết mình cần những giấy tờ gì cho tờ khai thuế 2024. Hỏi tôi vài câu rồi liệt kê
> giúp tôi."

Skill có sẵn phương án dự phòng cho đúng tình huống này: nó sẽ hỏi bạn về công việc, tình
trạng hôn nhân, con cái, thuế nhà thờ, tài khoản đầu tư, crypto, trợ cấp, hoá đơn thợ và chi
phí bệnh tật — rồi in ra danh sách giấy tờ.

### Cập nhật bản mới

Skill đã tải lên **không tự cập nhật**. Có bản mới thì tạo lại ZIP và tải lên đè, hoặc xoá
skill cũ rồi tải bản mới.

---

## 6. Cách C — Không cài gì cả

Bạn vẫn có thể đính kèm giấy tờ vào Claude và hỏi. Claude sẽ đọc và giải thích được các con
số, nói được về luật.

**Nhưng:** không có skill thì không có FIFO chính xác, không có đối chiếu tổng của broker
report, không có file CSV ELSTER, và không có bản kê những chỗ còn thiếu. Kết quả là **ước
lượng thô**. Dùng để hiểu tình hình thì được; **đừng lấy con số đó đi nộp.**

---

## 7. Quy trình sử dụng — ba bước

### Bước 1: Hỏi xem cần giấy tờ gì

| Cách A | Cách B |
|---|---|
| `/einstieg 2024` | „Tôi cần chuẩn bị giấy tờ gì cho tờ khai thuế 2024?" |

Claude chào bạn, giải thích trước sẽ có ba bước, rồi hỏi vài câu: đi làm công ăn lương hay tự
doanh, có kết hôn chưa, mấy con, có nộp thuế nhà thờ không, có tài khoản chứng khoán hay
crypto không, có nhận trợ cấp (thất nghiệp, nuôi con, ốm đau) không, năm rồi có thợ đến sửa
nhà hay có người giúp việc không, có chi phí bệnh tật lớn không.

Hai đến ba phút. Kết quả: **danh sách giấy tờ đúng cho hoàn cảnh của bạn**, các *Anlage* liên
quan và hạn nộp.

Bước này cũng cho biết bạn **có bắt buộc phải khai hay không**, và **những năm cũ còn khai
được** — khai tự nguyện được lùi lại **4 năm**, nên năm 2022 vẫn còn kịp. Nếu năm đó bạn đi
làm và bị trừ *Lohnsteuer*, khả năng cao là được hoàn tiền.

### Bước 2: Gom giấy tờ vào một thư mục

Bỏ tất cả vào chung một chỗ. PDF là đủ, bản scan cũng được (có OCR). Thường gồm:

| Giấy tờ | Từ đâu | Dùng cho |
|---|---|---|
| **Lohnsteuerbescheinigung** | công ty, thường vào tháng 1–2 | Anlage N + phần bảo hiểm |
| **Steuerbescheinigung** | ngân hàng, công ty môi giới | Anlage KAP, cả hai "hũ" lỗ |
| **Beitragsbescheinigung** | bảo hiểm y tế / hưu trí | Anlage Vorsorgeaufwand |
| **Report thuế của sàn** | Koinly, eToro, Kraken, Coinbase, Bitpanda, Binance | crypto & chứng khoán |
| Hoá đơn thợ, người giúp việc | tự lưu | § 35a — hoàn 20 % |
| Bảng quyết toán phí chung cư (*Nebenkosten*) | chủ nhà | § 35a — phần thợ nề, quét ống khói |
| Hoá đơn thuốc men, viện phí | tự lưu | § 33 |
| Chứng nhận quyên góp | tổ chức nhận | Sonderausgaben |

### Bước 3: Đưa hết cho Claude

| Cách A | Cách B |
|---|---|
| `/steuererklaerung 2024` + đính kèm file | đính kèm file + „Làm tờ khai thuế 2024 giúp tôi" |

Nói thêm hoàn cảnh trong một câu là đủ:

> „Độc thân, thuế nhà thờ 9 %, không con. Năm 2023 tôi còn 900 € lỗ theo § 23 chuyển sang."

**Bạn không phải gõ con số nào.** Claude nhận dạng từng file, đọc số ra, đối chiếu với phần
tổng mà chính report ghi, tính, rồi **chỉ hỏi những gì không có trên giấy tờ nào**: chi phí
đi lại và chi phí nghề nghiệp (*Werbungskosten*), hoá đơn § 35a, tiền quyên góp, họ tên và
mã số thuế.

Cuối cùng Claude **dẫn bạn đi từng dòng ELSTER**: mở đúng *Anlage*, đúng số dòng, điền đúng
con số.

---

## 8. Bạn nhận được gì

| File | Nội dung |
|---|---|
| `elster_mapping_2024.csv` | **thành quả chính** — mỗi dòng form ELSTER đúng một con số cần điền |
| `elster_mapping_2024.json` | cùng nội dung, dạng máy đọc, có ghi nguồn từng dòng |
| `taxreport_2024.html` | bảng tổng quan: các chỉ số, thu nhập theo từng Anlage, toàn bộ giao dịch bán, và **bản kê những chỗ còn chưa chắc** |
| `taxreport_2024.pdf` | bản in để lưu hoặc đưa cho kế toán thuế |

Kèm theo là **các cảnh báo**: Freigrenze suýt vượt, thiếu lịch sử mua crypto, xung đột thời
hạn nắm giữ, profile sàn chưa được kiểm chứng. Nếu phần đối chiếu tổng không khớp, chương
trình **dừng lại** chứ không đưa ra một con số nghe có vẻ hợp lý.

Bản báo cáo còn ghi rõ **nó sai lệch theo hướng nào**: mỗi chỗ còn thiếu đều được nêu là làm
thuế tăng hay giảm, kèm ước lượng độ lớn nếu suy ra được.

---

## 9. Khi có giấy báo thuế (*Steuerbescheid*)

Vài tuần sau khi nộp, Sở thuế gửi *Steuerbescheid* về. Đưa nó cho Claude:

| Cách A | Cách B |
|---|---|
| `/bescheid-pruefen bescheid.pdf` | đính kèm + „Kiểm tra giúp tôi giấy báo thuế này so với bản tính trước đó" |

Claude đối chiếu **từng khoản** với bản tính của bạn, chỉ ra chỗ lệch, **tính hạn khiếu nại**
(§ 355 AO: một tháng kể từ ngày được coi là đã nhận — ngày thứ 4 sau khi gửi, có tính ngày
nghỉ), và soạn sẵn đơn *Einspruch* nếu cần.

---

## 10. Bảng lệnh đầy đủ

| Lệnh (cách A) | Nói gì nếu dùng cách B | Để làm gì |
|---|---|---|
| `/einstieg` | „Tôi cần giấy tờ gì?" | **bắt đầu ở đây** — hỏi vài câu, ra danh sách giấy tờ |
| `/steuererklaerung` | „Làm tờ khai thuế 2024 giúp tôi" | toàn bộ quy trình: đọc, tính, xuất file, dẫn qua ELSTER |
| `/bescheid-pruefen` | „Kiểm tra giấy báo thuế này" | soi *Steuerbescheid*, tính hạn khiếu nại |
| `/krypto-check` | „Bán coin sau 8 tháng thì có phải nộp thuế không?" | hỏi lẻ một câu, không cần làm cả report |
| `/steuer-pruefen` | „Rà lại bản báo cáo này giúp tôi" | soát lại report trước khi gõ vào ELSTER |
| `/broker-profil` | „Thêm sàn X vào giúp tôi" | thêm sàn/broker mà plugin chưa biết |

---

## 11. Gặp trục trặc

| Hiện tượng | Nguyên nhân & cách xử lý |
|---|---|
| Gõ `/einstieg` không ra gì | Bạn đang ở Claude Desktop chứ không phải Claude Code → dùng **cách B**, nói bằng lời thường |
| Đã cài plugin mà vẫn không thấy lệnh | Plugin chỉ có hiệu lực ở **phiên mới** — mở chat mới |
| Cài bản mới mà vẫn là bản cũ | Lệnh update so *số phiên bản*, không so nội dung → chạy `/plugin marketplace update steuer-de` trước |
| „Không chạy được code" / không có file kết quả | Chưa bật **code execution** trong `Settings` → `Capabilities` |
| Tải ZIP lên bị từ chối | Trong ZIP phải là **thư mục** `steuererklaerung/`, bên trong có `SKILL.md`. Nén nhầm cấp là hỏng |
| „Không nhận dạng được file" | Claude sẽ nói **profile nào gần đúng nhất và thiếu dấu hiệu gì**. Giấy tờ lạ thì bị bỏ qua **có báo**, chứ không bị đoán bừa |
| PDF scan đọc thiếu dòng | Cần Tesseract kèm gói tiếng Đức. Chương trình sẽ báo nếu lớp chữ quá mỏng |
| Số tiền đọc ra sai | Cứ nói thẳng con số đúng cho Claude — nó ghi đè. Nhưng nên xem lại vì sao lệch |

---

## 12. Riêng tư

Giấy tờ thuế chứa **dữ liệu thật**: họ tên, mã số thuế, thu nhập.

- Plugin **không gửi dữ liệu của bạn đi đâu** và **không nộp hộ lên ELSTER**. Kết quả là file.
- File `steuerdaten.json` sinh ra trong quá trình làm chứa dữ liệu thật — **đừng** đẩy nó lên
  GitHub, đừng để trong thư mục chia sẻ đám mây, đừng gửi vào chat của người khác.
- Đương nhiên nội dung bạn đính kèm vào Claude thì có đi qua Claude — đó là điều kiện để nó
  đọc được. Nếu bạn không muốn vậy, cách duy nhất là làm tay.

---

## 13. Những gì nó **không** làm

- **Không phải tư vấn thuế**, không phải bản tính có giá trị pháp lý. ELSTER mới là nơi tính
  ràng buộc; phần soát cuối nên nhờ *Steuerberater*.
- **Chưa tính**: chi phí trông trẻ, trợ cấp cha mẹ đơn thân, trợ cấp học nghề, khấu trừ thuế
  doanh nghiệp, các khoản đã tạm nộp, phần giảm trần bảo hiểm cho công chức, FIFO theo từng
  ví riêng.
- **Ba profile sàn chưa được kiểm chứng** bằng file thật (Coinbase, Bitpanda, Binance) — vẫn
  chạy, nhưng có cảnh báo rõ. Đã kiểm chứng: Koinly, eToro, Kraken.
- Các profile đọc giấy chứng nhận được xây trên mẫu tự tạo. Gặp bản trình bày lạ có thể thiếu
  dòng — **chỉ những gì rõ ràng mới được lấy**: với *Lohnsteuerbescheinigung*, số hiệu ô
  **và** dòng chữ mô tả phải khớp nhau, không thì báo chứ không điền.

---

## 14. Giấy phép

MIT, không bảo hành. Mã nguồn: <https://github.com/truongtud/steuererklaerung-de>
