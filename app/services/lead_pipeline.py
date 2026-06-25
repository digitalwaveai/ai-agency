from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Lead
from app.schemas import LeadRead, SearchRequest
from app.services.deduplication import find_duplicate
from app.services.lead_enrichment import result_to_lead
from app.services.scoring import score_lead
from app.services.search_quality import offer_is_relevant, pain_is_confirmed
from app.services.search_service import SearchService
from app.services.site_enrichment import enrich_leads_from_web
from app.services.smart_analysis import analyze_and_filter_leads


async def search_and_save_leads(
    req: SearchRequest,
    db: Session,
) -> list[Lead]:
    results = await SearchService().search(req)
    lead_inputs = [result_to_lead(result, req) for result in results]
    lead_inputs = await enrich_leads_from_web(lead_inputs)
    lead_inputs = await analyze_and_filter_leads(
        lead_inputs,
        niche=req.niche,
        city=req.city,
        target_pain=req.target_pain,
        services=req.services,
        exclude=req.exclude,
        strict_match=req.strict_match,
    )

    effective_min_score = max(
        req.min_score,
        60 if req.strict_match else req.min_score,
    )
    qualified = []

    for lead_in in lead_inputs:
        lead_in.score, lead_in.score_reason = score_lead(lead_in, req)

        if lead_in.score < effective_min_score:
            continue

        if (
            req.strict_match
            and req.target_pain
            and not pain_is_confirmed(lead_in.pain_points)
        ):
            continue

        if (
            req.strict_match
            and req.services
            and not offer_is_relevant(lead_in.suggested_offer)
        ):
            continue

        has_contact = any(
            [
                lead_in.email,
                lead_in.phone,
                lead_in.telegram_url,
                lead_in.instagram_url,
                lead_in.vk_url,
                lead_in.whatsapp,
            ]
        )

        if req.contacts_only and not has_contact:
            continue

        qualified.append(lead_in)

    qualified.sort(key=lambda item: item.score, reverse=True)
    qualified = qualified[: req.limit]
    saved: list[Lead] = []

    for lead_in in qualified:
        duplicate = find_duplicate(db, lead_in)

        if duplicate:
            refresh_fields = (
                "name",
                "website_url",
                "instagram_url",
                "tiktok_url",
                "telegram_url",
                "vk_url",
                "youtube_url",
                "email",
                "phone",
                "whatsapp",
                "description",
                "pain_points",
                "suggested_offer",
                "source_url",
                "source_type",
                "score",
                "score_reason",
            )

            for field_name in refresh_fields:
                value = getattr(lead_in, field_name, None)
                if value not in (None, "", "не найден"):
                    setattr(duplicate, field_name, value)

            db.add(duplicate)
            db.commit()
            db.refresh(duplicate)
            saved.append(duplicate)
            continue

        lead = Lead(**lead_in.model_dump())
        db.add(lead)
        db.commit()
        db.refresh(lead)
        saved.append(lead)

    unique_leads = {lead.id: lead for lead in saved}
    return list(unique_leads.values())
