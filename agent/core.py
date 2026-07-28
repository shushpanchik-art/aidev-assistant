"""Agent Core (SPEC §1.3): план -> генерация -> gate -> фикс-цикл.

Оркестратор одной задачи. AI-вызовы идут через ai.gemini.generate_text,
исполнение проверок — через agent.gate.run_gate, файловые операции —
через agent.sandbox. Никаких прямых subprocess/секретов здесь нет.

Публичное API:
    extract_code(text)      — вытащить код из markdown-блока ответа модели.
    solve_task(...)         — прогнать полный цикл над одним файлом задачи.
    TaskOutcome             — результат (успех, итерации, финальный gate, diff).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from agent import gate, sandbox
from ai import gemini, prompts

logger = logging.getLogger(__name__)

_CODE_BLOCK = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def extract_code(text: str) -> str:
    """Вытащить содержимое первого markdown-блока кода из ответа модели.

    Если блока нет — возвращаем текст как есть (модель могла ответить голым
    кодом). Хвостовые пробелы срезаем, финальный перевод строки добавляем.
    """
    m = _CODE_BLOCK.search(text)
    body = m.group(1) if m else text
    return body.rstrip() + "\n"


@dataclass
class TaskOutcome:
    """Итог работы над одной задачей."""

    success: bool
    iterations: int
    gate: gate.GateResult
    diff: str = ""
    model: str = "flash"
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""
    steps: list[str] = field(default_factory=list)


def _gate_output(result: gate.GateResult) -> str:
    """Собрать текст ошибок красных шагов gate для передачи модели."""
    parts: list[str] = []
    for s in result.steps:
        if not s.passed and not s.skipped:
            parts.append(f"### {s.tool} (exit {s.exit_code})\n{s.output}")
    return "\n\n".join(parts)


def solve_task(
    task_id: int,
    task: str,
    rel_path: str,
    *,
    model: str = "flash",
    max_iterations: int = 4,
) -> TaskOutcome:
    """Полный цикл над одним файлом песочницы.

    Предполагается, что рабочая копия уже развёрнута (sandbox.prepare_workspace)
    и файл rel_path существует в каталоге задачи.

    1. Читает текущий код, просит модель внести правки (edit_prompt).
    2. Записывает результат, прогоняет gate.
    3. Пока gate красный и есть итерации — просит фикс (fix_prompt) по выводу
       проверок, перезаписывает файл, снова gate.
    4. Возвращает TaskOutcome с финальным состоянием и diff.
    """
    steps: list[str] = []
    in_tok = 0
    out_tok = 0
    model_used = model

    current = sandbox.read_file(task_id, rel_path)
    steps.append(f"Прочитан {rel_path} ({len(current)} символов)")

    gen = gemini.generate_text(
        prompts.edit_prompt(task, rel_path, current),
        model=model,
        system_instruction=prompts.SYSTEM_DEVELOPER,
    )
    in_tok += gen.input_tokens
    out_tok += gen.output_tokens
    model_used = gen.model
    current = extract_code(gen.text)
    sandbox.write_file(task_id, rel_path, current)
    steps.append("Внесена первичная правка")

    result = gate.run_gate(task_id)
    iterations = 0
    while not result.passed and iterations < max_iterations:
        iterations += 1
        steps.append(
            f"Gate красный ({', '.join(result.red_tools)}); "
            f"фикс-итерация {iterations}"
        )
        fix = gemini.generate_text(
            prompts.fix_prompt(rel_path, current, _gate_output(result)),
            model=model,
            system_instruction=prompts.SYSTEM_DEVELOPER,
        )
        in_tok += fix.input_tokens
        out_tok += fix.output_tokens
        model_used = fix.model
        current = extract_code(fix.text)
        sandbox.write_file(task_id, rel_path, current)
        result = gate.run_gate(task_id)

    if result.passed:
        steps.append(f"Gate зелёный: {result.summary()}")
    else:
        steps.append(
            f"Gate остался красным после {iterations} итераций: {result.summary()}"
        )

    diff = ""
    try:
        diff = sandbox.collect_diff(task_id)
    except sandbox.SandboxError as e:  # noqa: BLE001
        logger.warning("collect_diff не удался: %s", e)

    return TaskOutcome(
        success=result.passed,
        iterations=iterations,
        gate=result,
        diff=diff,
        model=model_used,
        input_tokens=in_tok,
        output_tokens=out_tok,
        steps=steps,
    )
