from datetime import timedelta

from django.utils import timezone

INTERVIEW_STATUSES = ('interviewing', 'online_interview', 'onsite_interview')


def calculate_health(applications, today=None):
    """Calculate an estimated progress indicator.

    Activity (40): recent volume 15, active weeks 15, recency 10.
    Interview conversion (25): target 30%, with five neutral prior observations.
    Offer conversion (15): target 40%, with three neutral prior observations.
    Follow-through (20): submitted records with notes, events, feedback, or progression.
    Neutral priors limit conclusions from tiny samples; pending responses are not failures.
    """
    today = today or timezone.localdate()
    rows = list(applications.prefetch_related('events'))
    submitted = [a for a in rows if a.status != 'preparing']
    n = len(submitted)
    recommendations = []

    if not n:
        return {
            'score': 0,
            'rating': 'Needs Attention',
            'submitted': 0,
            'interviews': 0,
            'offers': 0,
            'rejected': 0,
            'in_progress': 0,
            'recent': 0,
            'recommendations': [
                'Submit your first application when ready. More application data is needed for meaningful recommendations.'
            ]
        }
    interviews = [
        a
        for a in submitted
        if (
            a.reached_interview
            or a.status in INTERVIEW_STATUSES
            or any(
                e.event_type == 'interview'
                and e.event_date <= timezone.now()
                and timezone.localtime(e.event_date).date() <= today
                for e in a.events.all()
            )
        )
    ]
    offers = sum(a.status == 'offer' for a in submitted)
    interviewed_offers = sum(a.status == 'offer' for a in interviews)
    rejected = sum(a.status == 'rejected' for a in submitted)
    dates = [
        a.date_submitted
        for a in submitted
        if a.date_submitted and a.date_submitted <= today
    ]
    recent = [d for d in dates if d >= today - timedelta(days=27)]
    weeks = {(today - d).days // 7 for d in recent}
    age = (today - max(dates)).days if dates else 999
    activity = (
        15 * min(len(recent) / 8, 1)
        + 15 * len(weeks) / 4
        + 10 * max(0, 1 - age / 28)
    )

    # Resolved applications and actual interviews are evidence; awaiting replies are not failures.
    resolved = sum(
        a.status in ('offer', 'rejected') or a in interviews for a in submitted
    )
    interview_points = 25 * min(((len(interviews) + 0.75) / (resolved + 5)) / 0.30, 1)
    resolved_interviews = sum(a.status in ('offer', 'rejected') for a in interviews)
    offer_points = 15 * min(
        ((interviewed_offers + 0.6) / (resolved_interviews + 3)) / 0.40,
        1
    )
    followed = sum(
        bool(
            a.notes
            or a.rejection_feedback
            or a.events.all()
            or a.status in INTERVIEW_STATUSES
            or a.status == 'offer'
        )
        for a in submitted
    )
    follow_points = 20 * followed / n * min(n / 5, 1)
    score = max(
        0,
        min(
            100,
            round(activity + interview_points + offer_points + follow_points)
        )
    )
    rating = (
        'Excellent' if score >= 80
        else 'Good' if score >= 60
        else 'Developing' if score >= 40
        else 'Needs Attention'
    )

    if n < 5:
        recommendations.append(
            'Your sample is still small. Keep tracking applications before drawing conclusions about conversion rates.'
        )
    if len(recent) < 4 or age > 14:
        recommendations.append(
            'Set a manageable weekly application goal and look for additional suitable opportunities.'
        )
    if n >= 10 and resolved >= 5 and len(interviews) / n < 0.2:
        recommendations.append(
            'Few applications have reached interviews. Tailor your resume and keywords to each role and review the qualifications. Pending replies may still progress.'
        )
    if (
        len(interviews) >= 3
        and resolved_interviews >= 3
        and interviewed_offers / resolved_interviews < 0.25
    ):
        recommendations.append(
            'Practice behavioral and technical questions in mock interviews and research each company before interviewing.'
        )
    if sum(bool(a.rejection_feedback) for a in submitted) >= 2:
        recommendations.append(
            'Review your saved employer feedback for recurring themes to guide your preparation.'
        )
    if followed < n / 2:
        recommendations.append(
            'Record next steps, notes, or follow-up events to keep applications moving.'
        )
    if len(recent) >= 4 and len(interviews) >= 3 and offers:
        recommendations.append(
            'You are making progress through applications, interviews, and offers. Maintain your routine and keep tracking outcomes.'
        )
    if not recommendations:
        recommendations.append(
            'Keep tracking outcomes and following up on applications while employers consider them.'
        )
    return {
        'score': score,
        'rating': rating,
        'submitted': n,
        'interviews': len(interviews),
        'offers': offers,
        'rejected': rejected,
        'in_progress': n - offers - rejected,
        'recent': len(recent),
        'recommendations': recommendations
    }
