"""Сценарий (см. README.md): правка профиля пользователем в личном кабинете (приоритет над каталогом) и «Восстановить из LDAP»; политика — в админке."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

ROW = lambda label: f"[...document.querySelectorAll('.profile-dl > div')].find(d => d.querySelector('dt')?.textContent.trim() === {label!r})"


async def main():
    A = S.Client("alice", "alice", "alice-pass", 9495)
    try:
        await A.start()
        await A.goto("/profile", 2.5)
        S.check("личный кабинет открыт, поле «Должность» показано", await A.wait_for(f"!!({ROW('Должность')})", 20))
        # по умолчанию редактируются должность, подразделение и телефон; ФИО и e-mail — нет
        S.check("у «Должность» есть кнопка «Изменить»", await A.js(f"!![...({ROW('Должность')}).querySelectorAll('button')].find(b => b.textContent.trim() === 'Изменить')"))
        S.check("у «ФИО» кнопки «Изменить» нет (запрещено политикой)", await A.js(f"!([...({ROW('ФИО')}).querySelectorAll('button')].find(b => b.textContent.trim() === 'Изменить'))"))
        await A.js(f"[...({ROW('Должность')}).querySelectorAll('button')].find(b => b.textContent.trim() === 'Изменить').click()")
        await asyncio.sleep(0.4)
        await A.js("""(() => { const i = document.querySelector('.profile-dl input'); const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; set.call(i, 'Главный по переговоркам'); i.dispatchEvent(new Event('input', {bubbles: true})); })()""")
        await asyncio.sleep(0.3)
        await A.js("[...document.querySelectorAll('.profile-dl button')].find(b => b.textContent.trim() === 'Сохранить').click()")
        S.check("после сохранения значение показано с отметкой «изменено вами»", await A.wait_for(f"(() => {{ const d = {ROW('Должность')}; return !!d && d.textContent.includes('Главный по переговоркам') && d.textContent.includes('изменено вами'); }})()", 10))
        S.check("рядом показано значение каталога и кнопка «Восстановить из LDAP»", await A.js(f"(() => {{ const d = {ROW('Должность')}; return d.textContent.includes('В каталоге:') && [...d.querySelectorAll('button')].some(b => b.textContent.includes('Восстановить из LDAP')); }})()"))
        await A.shot("profile-edited")
        # «Обновить данные из AD» не стирает правку
        await A.js("[...document.querySelectorAll('button')].find(b => b.textContent.includes('Обновить данные из Active Directory')).click()")
        await asyncio.sleep(2.5)
        S.check("после «Обновить из AD» правка сохранилась", await A.js(f"{ROW('Должность')}.textContent.includes('Главный по переговоркам')"))
        # после перезагрузки страницы — тоже
        await A.goto("/profile", 2.5)
        S.check("после перезагрузки страницы правка на месте", await A.wait_for(f"!!({ROW('Должность')}) && {ROW('Должность')}.textContent.includes('Главный по переговоркам')", 15))
        await A.js(f"[...({ROW('Должность')}).querySelectorAll('button')].find(b => b.textContent.includes('Восстановить из LDAP')).click()")
        S.check("«Восстановить из LDAP» снимает отметку", await A.wait_for(f"(() => {{ const d = {ROW('Должность')}; return !!d && !d.textContent.includes('изменено вами') && !d.textContent.includes('Главный по переговоркам'); }})()", 10))
        await A.shot("profile-restored")
        S.check("ошибок страницы нет", not (await A.state())["errs"], str((await A.state())["errs"][:2]))
    finally:
        await A.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
