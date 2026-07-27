# AIDEV — онбординг сессии разработки

## Что это
Приватный AI-помощник разработчика. Полный контекст и правила: `docs/SPEC.md` (истина проекта).

## Разведка в начале КАЖДОЙ сессии (копипаст)
```bash
cd /opt/aidev
free -h; df -h /
cat docs/SPEC.md | head -60
grep -n '^## ' docs/SPEC.md
ls -R --ignore=venv --ignore=__pycache__ .
git -C /opt/aidev status 2>/dev/null || echo "git ещё не init"
systemctl is-active smoki-bot

Инварианты (нельзя нарушать)

Своп: 2 файла (/swapfile + /swapfile2 = 4 ГБ). Проверка: swapon --show.
Боты SMOKI не трогать. aidev.service лимит MemoryMax=400M.
Веб слушает только 127.0.0.1:8090. Наружу НЕ публиковать.
Правки файлов — только скриптом patch.py с assert old in s, не руками в nano.
main protected: ветка → PR → CI → merge. Прямых коммитов в main нет.
Ключи Gemini берём из SMOKI .env (Vertex), НЕ дублируем в открытом виде.
Порядок работы (git flow)

git checkout -b feature|fix|chore|docs/<name>
правки → venv/bin/ruff check <файлы> && venv/bin/python -m mypy <файлы>
git add -p && git commit && git push -u origin <ветка>
gh pr create → ждать зелёный CI → merge через UI
git checkout main && git pull && git branch -d <ветка> && git remote prune origin
Текущий статус (обновлять в конце сессии)

 Каталоги /opt/aidev созданы
 SPEC.md записан (18 разделов)
 Своп 4 ГБ
 Git init + первый коммит + private repo aidev-assistant
 venv aidev + зависимости
 Модуль ai/ (обёртка google-genai Vertex)
 Executor + sandbox
 FastAPI web/ на 8090
 systemd aidev.service
 CI (.github/workflows)
Приёмка «готов к разработке»


Разработка стартует, когда: своп 4 ГБ + git repo + venv + ai/ проходит smoke
(venv/bin/python -c "from ai import generate_text" без ошибок) + один зелёный PR.


cd /opt/aidev && python3 -c "import re;p='docs/ONBOARDING.md';s=open(p).read();open(p,'w').write(re.sub(r'\n{3,}','\n\n',s).rstrip(chr(10))+chr(10))" && \
echo "=== разделы онбординга ===" && grep -n '^## ' docs/ONBOARDING.md && wc -l docs/ONBOARDING.md
