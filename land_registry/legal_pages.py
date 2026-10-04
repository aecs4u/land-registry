"""Static informational pages linked from the footer and cookie banner.

Each page is ``(title, lead, sections)`` with plain English msgids; the
template runs them through gettext so the Italian catalogue can translate
them. Facts here describe what the application actually does; the data
controller should review the wording before relying on it as a legal text.
"""

import os

PAGE_KEYS = ("privacy", "terms", "help", "contact", "notifications")


def _contact_line(_) -> str:
    email = os.getenv("LEGAL_CONTACT_EMAIL", "").strip()
    if email:
        return _("Write to {email} and we will reply as soon as we can.").format(email=email)
    return _("Use the contact details published on the aecs4u.it website.")


def build_page(key: str, _) -> dict:
    if key == "privacy":
        return {
            "page_title": _("Privacy"),
            "lead": _("What this application stores and which third parties see your requests."),
            "sections": [
                {"heading": _("Account"), "paragraphs": [
                    _("Signing in is handled by our identity provider (Clerk). We receive your name and email address and keep a session cookie so you stay signed in."),
                ]},
                {"heading": _("Your files and shortlist"), "paragraphs": [
                    _("Files you upload and parcels you shortlist are stored against your account and are not shared with other users."),
                ]},
                {"heading": _("Cookies and local storage"), "paragraphs": [
                    _("We use cookies and browser storage for the session, the interface language and the light or dark theme. We do not use advertising cookies."),
                ]},
                {"heading": _("Third-party services"), "paragraphs": [
                    _("Basemap tiles are loaded from third-party tile providers, and place-name searches that find no cadastral match are sent to OpenStreetMap Nominatim. Those providers see your IP address and the text you searched for."),
                ]},
                {"heading": _("Diagnostics"), "paragraphs": [
                    _("Request counts and latency buckets are recorded for the map endpoints. Search text and coordinates are not part of those metrics."),
                ]},
                {"heading": _("Your rights"), "paragraphs": [
                    _("You can ask to access, correct or delete your data at any time."),
                    _contact_line(_),
                ]},
            ],
        }
    if key == "terms":
        return {
            "page_title": _("Terms"),
            "lead": _("The conditions for using the Land Registry Viewer."),
            "sections": [
                {"heading": _("Informational use"), "paragraphs": [
                    _("Cadastral and market data are shown for information only. They are not a substitute for an official extract from the Agenzia delle Entrate, and parcel boundaries are indicative."),
                ]},
                {"heading": _("Acceptable use"), "paragraphs": [
                    _("Do not attempt to overload the service, scrape it in bulk, or use it to access data you are not entitled to."),
                ]},
                {"heading": _("Availability"), "paragraphs": [
                    _("The service is provided as is. Layers can be temporarily unavailable when an upstream data source is down."),
                ]},
            ],
        }
    if key == "help":
        return {
            "page_title": _("Help"),
            "lead": _("A short guide to the map."),
            "sections": [
                {"heading": _("Finding a place or parcel"), "paragraphs": [
                    _("Type a municipality name, or a parcel reference such as A001_000100.12, into the search box and choose a result."),
                ]},
                {"heading": _("Layers"), "paragraphs": [
                    _("Open the layers panel to switch data on and off. Cadastral parcels appear when you zoom in to a municipality. A greyed-out layer has no data available at the moment."),
                ]},
                {"heading": _("Selecting parcels"), "paragraphs": [
                    _("Click a parcel to see its details and add it to your shortlist. Signing in keeps the shortlist between visits."),
                ]},
                {"heading": _("Map not loading?"), "paragraphs": [
                    _("If the map reports that its data source is unavailable, wait a moment and pan or zoom to retry."),
                    _contact_line(_),
                ]},
            ],
        }
    if key == "contact":
        return {
            "page_title": _("Contact"),
            "lead": _("Questions, corrections and data requests."),
            "sections": [
                {"heading": _("Get in touch"), "paragraphs": [_contact_line(_)]},
            ],
        }
    if key == "notifications":
        return {
            "page_title": _("Notifications"),
            "lead": _("You have no notifications."),
            "sections": [],
        }
    raise KeyError(key)
