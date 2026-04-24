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

_SYSTEM_HR = """You are an expert running coach with decades of experience training athletes \
from beginners to sub-elite. Generate a detailed, personalised week-by-week training plan \
structured around HEART RATE ZONES and TIME-based workouts.

Return ONLY a single valid JSON object — no markdown fences, no explanation, no extra text.

Required JSON structure:
{
  "title": "string — concise plan name",
  "summary": "string — 2-3 sentence overview",
  "plan_type": "hr",
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
          "day": "string — full day name",
          "type": "string — Easy Run | Long Run | Tempo Run | Interval | Race Pace | Fartlek | Cross Training | Strength | Rest",
          "duration_min": number (minutes; use 0 for Rest/Cross Training),
          "distance_km": number (approximate km for context only; use 0 for Rest/Cross Training),
          "effort": "string — one of: easy | moderate | tempo | hard | race | rest",
          "hr_zone": integer (1-5; omit for Rest/Cross Training/Strength),
          "notes": "string — specific coaching cue focused on feel and effort (no bpm ranges)"
        }
      ]
    }
  ]
}

Rules:
- Include ALL 7 days in every week's runs array
- Rest days: duration_min 0, distance_km 0, effort "rest", omit hr_zone
- HR zone guide: 1=very easy recovery, 2=easy aerobic, 3=moderate aerobic, 4=threshold/tempo, 5=VO2max/race
- Easy runs → Zone 2, Long runs → Zone 2, Tempo → Zone 4, Intervals → Zone 4-5, Recovery → Zone 1
- Increase mileage gradually — never more than 10-15% per week during Build phase
- For races over 10K: include a proper taper (2-3 weeks reducing volume by 20-30%)
- Long runs on the athlete's preferred long run day
- Keep notes focused on feel: "comfortable conversational effort", "comfortably hard", etc.
"""

_SYSTEM_PACE = """You are an expert running coach with decades of experience training athletes \
from beginners to sub-elite. Generate a detailed, personalised week-by-week training plan \
structured around DISTANCE and PACE targets.

Return ONLY a single valid JSON object — no markdown fences, no explanation, no extra text.

Required JSON structure:
{
  "title": "string — concise plan name",
  "summary": "string — 2-3 sentence overview",
  "plan_type": "pace",
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
          "day": "string — full day name",
          "type": "string — Easy Run | Long Run | Tempo Run | Interval | Race Pace | Fartlek | Cross Training | Strength | Rest",
          "distance_km": number (use 0 for Rest/Cross Training/Strength),
          "effort": "string — one of: easy | moderate | tempo | hard | race | rest",
          "pace_slow": "string — slowest acceptable pace in M:SS format (e.g. '6:30'); omit for Rest/Cross Training/Strength/Interval",
          "pace_fast": "string — fastest acceptable pace in M:SS format (e.g. '6:00'); omit for Rest/Cross Training/Strength/Interval",
          "notes": "string — specific coaching cue or interval structure (e.g. '6 × 800m at 5K pace, 90sec recovery jog')"
        }
      ]
    }
  ]
}

Rules:
- Include ALL 7 days in every week's runs array
- Rest days: distance_km 0, effort "rest", omit pace fields
- Calibrate paces to the athlete's recent run history — use their actual recent paces as reference
- Easy pace ≈ 60-90sec/km slower than 5K race pace; Long run pace ≈ 45-75sec/km slower than marathon pace
- Tempo pace ≈ comfortably hard, sustainable for ~20-40min
- For intervals: omit pace_slow/pace_fast, put the full structure in notes instead
- Increase mileage gradually — never more than 10-15% per week during Build phase
- For races over 10K: include a proper taper (2-3 weeks reducing volume by 20-30%)
- Long runs on the athlete's preferred long run day
"""


MIN_WEEKS = 4
MAX_WEEKS = 32


def _weeks_until(race_date_str: str) -> int:
    target = date.fromisoformat(race_date_str)
    delta = (target - date.today()).days
    return min(delta // 7, MAX_WEEKS)


def _build_prompt(
    race_goal: str,
    race_date: str | None,
    days_per_week: int,
    current_weekly_km: float,
    long_run_day: str,
    fitness_context: str,
    plan_type: str = "pace",
) -> str:
    if race_date:
        weeks = _weeks_until(race_date)
        date_line = f"Race date: {race_date} ({weeks} weeks from today) — plan must be exactly {weeks} weeks"
    else:
        weeks = 12
        date_line = f"No specific race date — generate a {weeks}-week base-building plan toward the goal"

    style_note = (
        "Structure every run using TIME (duration_min) and HR ZONE — do NOT include pace targets."
        if plan_type == "hr" else
        "Structure every run using DISTANCE (distance_km) and PACE (pace_slow/pace_fast) — calibrate paces to the athlete's recent runs."
    )

    return f"""Create a training plan for the following athlete:

GOAL: {race_goal}
{date_line}
AVAILABLE TRAINING DAYS: {days_per_week} days per week
CURRENT WEEKLY MILEAGE: {current_weekly_km:.0f} km/week
PREFERRED LONG RUN DAY: {long_run_day}
PLAN STYLE: {style_note}

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
    plan_type: str = "pace",
) -> dict:
    llm = ChatGoogleGenerativeAI(
        model=MODEL,
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        temperature=0.4,
    )

    system = _SYSTEM_HR if plan_type == "hr" else _SYSTEM_PACE
    prompt = _build_prompt(
        race_goal, race_date, days_per_week, current_weekly_km, long_run_day,
        fitness_context, plan_type,
    )

    log.info(
        "Generating plan: goal=%r date=%s days/wk=%d km/wk=%.0f type=%s",
        race_goal, race_date, days_per_week, current_weekly_km, plan_type,
    )

    response = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=prompt),
    ])

    plan = _parse_json(response.content)
    log.info("Plan generated: %d weeks, peak %.0fkm", plan.get("total_weeks", 0), plan.get("peak_weekly_km", 0))
    return plan
