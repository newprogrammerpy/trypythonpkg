# pypack: đóng gói Python trên Windows x64

`pypack.py` tạo gói `.pypkg`, file `.exe` Windows x64 tự chứa CPython, và MSI
cài file `.exe` vào hồ sơ người dùng. Công cụ không dùng PyInstaller hoặc
Nuitka; lệnh `exe` cần CPython x64, MSVC x64 và Windows SDK.

## Tạo file `.exe`

Mở x64 Native Tools Command Prompt for Visual Studio, rồi chạy:

```powershell
python pypack.py exe .\main.py .\dist\main.exe
```

Để thêm module hoặc tài nguyên nằm trong thư mục của `main.py`, khai báo từng
file hay thư mục bằng `--include`:

```powershell
python pypack.py exe .\src\main.py .\dist\main.exe `
  --include package `
  --include assets\settings.json
```

Đường dẫn `--include` tương đối được tính từ thư mục chứa file nguồn. Công cụ
giữ cấu trúc thư mục khi đóng gói; không tự tìm hoặc cài thư viện bên thứ ba.
Hãy đưa những module và tài nguyên cần thiết vào `--include`.

File `.exe` chứa CPython và standard library. Khi chạy, launcher giải nén
runtime vào thư mục tạm, bật UTF-8 cho Python, chuyển tiếp đối số/mã thoát của
chương trình rồi dọn thư mục tạm. Nội dung có thể được trích xuất; đây không
phải cơ chế mã hóa hay chống dịch ngược.

## Tạo MSI

MSI đóng gói một file `.exe` đã tạo, không phải trực tiếp từ `.py`:

```powershell
python pypack.py msi .\dist\main.exe .\dist\main.msi `
  --name "My App" `
  --manufacturer "Example" `
  --version 1.0.0
```

MSI hiện cài theo phạm vi người dùng hiện tại vào `%LOCALAPPDATA%`; chưa có
shortcut, cài đặt toàn máy, MSIX hay ký số. Cần WiX Toolset 7.0.1. Nếu người
dùng chưa chấp thuận OSMF EULA, phải tự đọc và chấp thuận trước khi build; bộ
đóng gói không tự chấp thuận điều khoản thay người dùng.

## Tạo và chạy `.pypkg`

```powershell
python pypack.py create .\main.py .\dist\main.pypkg --include package
python pypack.py run .\dist\main.pypkg --help
```

`.pypkg` xác minh SHA-256 cho các file trong manifest. Hash phát hiện nội dung
không khớp manifest, nhưng không chứng minh ai tạo gói hay bảo vệ gói khỏi việc
bị sửa cả manifest lẫn nội dung.

## Phạm vi hiện tại

- Tạo `.exe` và MSI: Windows x64.
- Nguồn: một file `.py` làm entry point; file Python/tài nguyên phụ được chọn
  tường minh bằng `--include`.
- Chưa tự giải quyết dependencies, chưa bảo đảm ứng dụng bên thứ ba nào cũng
  chạy được, và chưa hỗ trợ các định dạng cài đặt/nền tảng khác.

## GitHub Actions

Workflow `.github/workflows/build.yml` chạy unit test trên Windows, Linux và
macOS; trên Windows runner, nó build/chạy một ứng dụng thử bằng lệnh `exe` rồi
đưa `main.exe` lên dưới dạng artifact của lần chạy workflow. Mở tab **Actions**
của repository để xem kết quả và tải artifact.

Hiện workflow chỉ tạo executable Windows x64. Các target ARM64, Android và
Apple có preflight/scaffold riêng, nhưng chưa được nối thành các job phát hành
artifact trong workflow này.
