import MetaTrader5 as mt5
print('init:', mt5.initialize(path=r'C:\Program Files\MetaTrader 5\terminal64.exe'))
print('err:', mt5.last_error())
print('login:', mt5.login(26578318, password='321Trade!@', server='VTMarkets-Live 3'))
print('err:', mt5.last_error())
ac = mt5.account_info()
if ac:
    print(f'Connected: #{ac.login} Balance: ')
else:
    print('No account info')
mt5.shutdown()
