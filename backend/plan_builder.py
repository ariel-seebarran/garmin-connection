"""
Training plan generator — calls the LLM with a structured prompt and returns
a week-by-week JSON plan personalised to the athlete's current fitness.
"""

import json
import os
import re
from datetime import date, timedelta

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

from logging_config import get_logger

log = get_logger("plan_builder")

MODEL = "gemini-2.5-flash"

_SYSTEM = """You are an expert running coach with decades of experience training athletes \
from beginners to sub-elite. Generate a detailed, personalised week-by-week training plan.

Return ONLY a single valid JSON object — no markdown fences, no explanation, no extra text.

Required JSON structure:
{
  "title": "string — concise plan name (e.g. '16-Week Sub-4:00 Marathon Plan')",
  "summary": "string — 2-3 sentence overview of the plan philosophy and key workouts",
  "total_weeks": integer,
  "peak_weekly_km": number,
  "weeks": [
    {
      "week_number": integer,
      "phase": "string — one of: Base | Build | Peak | Taper | Race",
      "theme": "string — one-line focus for the week",
      "total_km": number,
      "runs": [
        {
          "day": "string — full day name: Monday/Tuesday/Wednesday/Thursday/Friday/Saturday/Sunday",
          "type": "string — Easy Run | Long Run | Tempo Run | Interval | Race Pace | Fartlek | Cross Training | Strength | Rest",
          "distance_km": number (use 0 for Rest and Cross Training days),
          "effort": "string — one of: easy | moderate | tempo | hard | race | rest",
          "notes": "string — specific coaching cue, target pace zone, or workout structure (empty string if none)"
        }
      ]
    }
  ]
}

Rules:
- Include ALL 7 days in every week's runs array
- Rest days use type "Rest", effort "rest", distance_km 0
- Increase mileage gradually — never more than 10-15% per week during Build phase
- Include 1-2 rest or easy cross-training days per week
- For races over 10K: include a proper taper (2-3 weeks reducing volume by 20-30% each week)
- Tailor workout types and paces to the athlete's current fitness context
- Long runs on the athlete's preferred long run day
- Keep notes actionable and specific (e.g. "6km at lactate threshold pace, target HR 160-170bpm")
"""


def _weeks_until(race_date_str: str) -> int:
    try:
        target = date.fromisoformat(race_date_str)
        delta = (target - date.today()).days
        return max(4, min(delta // 7, 32))
    except (ValueError, TypeError):
        return 12


def _build_prompt(
    race_goal: str,
    race_date: str | None,
    days_per_week: int,
    current_weekly_km: float,
    long_run_day: str,
    fitness_context: str,
) -> str:
    if race_date:
        weeks = _weeks_until(race_date)
        date_line = f"Race date: {race_date} ({weeks} weeks from today) — plan must be exactly {weeks} weeks"
    else:
        weeks = 12
        date_line = f"No specific race date — generate a {weeks}-week base-building plan toward the goal"

    return f"""Create a training plan for the following athlete:

GOAL: {race_goal}
{date_line}
AVAILABLE TRAINING DAYS: {days_per_week} days per week
CURRENT WEEKLY MILEAGE: {current_weekly_km:.0f} km/week
PREFERRED LONG RUN DAY: {long_run_day}

ATHLETE FITNESS CONTEXT:
{fitness_context}

Generate a complete {weeks}-week plan. Personalise it to the athlete's current fitness, \
build progressively, and structure it so they peak at the right time."""


def _parse_json(text: str) -> dict:
    """Strip markdown fences if present and parse JSON."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ```
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text.strip())


async def generate_training_plan(
    race_goal: str,
    race_date: str | None,
    days_per_week: int,
    current_weekly_km: float,
    long_run_day: str,
    fitness_context: str,
) -> dict:
    llm = ChatGoogleGenerativeAI(
        model=MODEL,
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        temperature=0.4,
    )

    prompt = _build_prompt(
        race_goal, race_date, days_per_week, current_weekly_km, long_run_day, fitness_context
    )

    log.info(
        "Generating plan: goal=%r date=%s days/wk=%d km/wk=%.0f",
        race_goal, race_date, days_per_week, current_weekly_km,
    )

    response = await llm.ainvoke([
        SystemMessage(content=_SYSTEM),
        HumanMessage(content=prompt),
    ])

    plan = _parse_json(response.content)
    log.info("Plan generated: %d weeks, peak %.0fkm", plan.get("total_weeks", 0), plan.get("peak_weekly_km", 0))
    return plan
