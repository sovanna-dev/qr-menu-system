# generate_test_qrs.py
import qrcode

# Create test QR codes with bank names
for bank in ['aba', 'acleda', 'wing']:
    qr = qrcode.make(f"Pay to Restaurant: Bank {bank.upper()}\nAccount: 000123456\nAmount: [ENTER AMOUNT]")
    qr.save(f'static/{bank}_qr.png')
    print(f"Created {bank}_qr.png")