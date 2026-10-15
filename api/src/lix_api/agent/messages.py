"""The assistant's own words (not the model's) in the interface language.

The model answers in the language the page asks for (see ``current_state`` in agent.py);
these are the fixed texts the server itself sends: what to do when a run fails.
English is the fallback for any language or key without a translation.
"""

LANGUAGE_NAMES = {
    "en": "English",
    "cy": "Welsh (Cymraeg)",
    "gd": "Scottish Gaelic (Gàidhlig)",
    "ga": "Irish (Gaeilge)",
}

MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "still_works": "The map, weights and area pages still work.",
        "usage_limit": "That needed more steps than I'm allowed for one question, so I stopped. "
        "Try asking for one thing at a time.",
        "tool_retry": "I couldn't get the data I needed for that. Try rephrasing, or name a town "
        "or postcode.",
        "key_rejected": "The assistant's API key was rejected; check it in .env. {still_works}",
        "out_of_credit": "The assistant's model account is out of credit. {still_works}",
        "busy": "The language model is busy right now; try again in a minute. {still_works}",
        "model_problem": "The language model service had a problem; try again. {still_works}",
        "no_response": "The language model didn't respond; try again in a moment. {still_works}",
        "unknown": "Something went wrong while I was answering; try asking again. {still_works}",
        # A profile built by matching words: {preset}, {themes}, {indicators} are labels
        "profile_rationale": "Starts from {preset}; {themes} count more, and {indicators} weigh "
        "more within their themes.",
        "profile_rationale_themes": "Starts from {preset}; {themes} count more.",
        "profile_rationale_less": "Starts from {preset}; {themes} count less.",
        "profile_rationale_indicators": "Starts from {preset}; {indicators} weigh more within "
        "their themes.",
        "profile_rationale_plain": "Nothing in the description matched a theme or measure, so "
        "it starts from {preset}.",
    },
    "cy": {
        "still_works": "Mae'r map, y pwysau a thudalennau'r ardaloedd yn dal i weithio.",
        "usage_limit": "Roedd angen mwy o gamau ar hynny nag a ganiateir i mi ar gyfer un "
        "cwestiwn, felly rhoddais y gorau iddi. Ceisiwch ofyn am un peth ar y tro.",
        "tool_retry": "Methais gael y data roedd ei angen arnaf ar gyfer hynny. Ceisiwch ei "
        "aralleirio, neu enwch dref neu god post.",
        "key_rejected": "Gwrthodwyd allwedd API y cynorthwyydd; gwiriwch hi yn .env. {still_works}",
        "out_of_credit": "Mae cyfrif model y cynorthwyydd wedi rhedeg allan o gredyd. "
        "{still_works}",
        "busy": "Mae'r model iaith yn brysur ar hyn o bryd; rhowch gynnig arall arni mewn munud. "
        "{still_works}",
        "model_problem": "Cafodd gwasanaeth y model iaith broblem; rhowch gynnig arall arni. "
        "{still_works}",
        "no_response": "Ni ymatebodd y model iaith; rhowch gynnig arall arni mewn eiliad. "
        "{still_works}",
        "unknown": "Aeth rhywbeth o'i le wrth i mi ateb; ceisiwch ofyn eto. {still_works}",
        "profile_rationale": "Yn dechrau o {preset}; mae {themes} yn cyfrif mwy, ac mae "
        "{indicators} yn pwyso mwy o fewn eu themâu.",
        "profile_rationale_themes": "Yn dechrau o {preset}; mae {themes} yn cyfrif mwy.",
        "profile_rationale_less": "Yn dechrau o {preset}; mae {themes} yn cyfrif llai.",
        "profile_rationale_indicators": "Yn dechrau o {preset}; mae {indicators} yn pwyso mwy o "
        "fewn eu themâu.",
        "profile_rationale_plain": "Nid oedd dim yn y disgrifiad yn cyfateb i thema na mesur, "
        "felly mae'n dechrau o {preset}.",
    },
    "gd": {
        "still_works": "Tha am mapa, na cuideaman agus duilleagan nan sgìrean ag obair fhathast.",
        "usage_limit": "Bha feum air barrachd cheumannan na tha ceadaichte dhomh airson aon "
        "cheist, mar sin stad mi. Feuch ri aon rud iarraidh aig an aon àm.",
        "tool_retry": "Cha b' urrainn dhomh an dàta a bha a dhìth orm fhaighinn airson sin. "
        "Feuch ri a chur ann an dòigh eile, no ainmich baile no còd-puist.",
        "key_rejected": "Chaidh iuchair API a' chuidiche a dhiùltadh; thoir sùil oirre ann an "
        ".env. {still_works}",
        "out_of_credit": "Tha cunntas modail a' chuidiche air ruith a-mach à creideas. "
        "{still_works}",
        "busy": "Tha am modail cànain trang an-dràsta; feuch a-rithist ann am mionaid. "
        "{still_works}",
        "model_problem": "Bha duilgheadas aig seirbheis a' mhodail chànain; feuch a-rithist. "
        "{still_works}",
        "no_response": "Cha do fhreagair am modail cànain; feuch a-rithist ann an tiotan. "
        "{still_works}",
        "unknown": "Chaidh rudeigin ceàrr fhad 's a bha mi a' freagairt; feuch ri faighneachd "
        "a-rithist. {still_works}",
        "profile_rationale": "A' tòiseachadh bho {preset}; tha {themes} a' cunntadh barrachd, "
        "agus tha {indicators} nas truime am broinn an cuspairean.",
        "profile_rationale_themes": "A' tòiseachadh bho {preset}; tha {themes} a' cunntadh "
        "barrachd.",
        "profile_rationale_less": "A' tòiseachadh bho {preset}; tha {themes} a' cunntadh nas "
        "lugha.",
        "profile_rationale_indicators": "A' tòiseachadh bho {preset}; tha {indicators} nas "
        "truime am broinn an cuspairean.",
        "profile_rationale_plain": "Cha do mhaids dad san tuairisgeul cuspair no tomhas, mar sin "
        "tha e a' tòiseachadh bho {preset}.",
    },
    "ga": {
        "still_works": "Oibríonn an léarscáil, na hualaí agus leathanaigh na gceantar i gcónaí.",
        "usage_limit": "Theastaigh níos mó céimeanna uaidh sin ná mar atá ceadaithe dom d'aon "
        "cheist amháin, mar sin stop mé. Bain triail as rud amháin a iarraidh ag an am.",
        "tool_retry": "Níorbh fhéidir liom na sonraí a bhí uaim a fháil chuige sin. Bain triail "
        "as é a chur i bhfocail eile, nó ainmnigh baile nó postchód.",
        "key_rejected": "Diúltaíodh eochair API an chúntóra; seiceáil in .env í. {still_works}",
        "out_of_credit": "Tá cuntas samhla an chúntóra as creidmheas. {still_works}",
        "busy": "Tá an tsamhail teanga gnóthach faoi láthair; bain triail eile as i gceann "
        "nóiméid. {still_works}",
        "model_problem": "Bhí fadhb ag seirbhís na samhla teanga; bain triail eile as. "
        "{still_works}",
        "no_response": "Níor fhreagair an tsamhail teanga; bain triail eile as i gceann "
        "tamaill. {still_works}",
        "unknown": "Chuaigh rud éigin amú agus mé ag freagairt; bain triail as fiafraí arís. "
        "{still_works}",
        "profile_rationale": "Ag tosú ó {preset}; comhaireann {themes} níos mó, agus tá níos "
        "mó meáchain ag {indicators} laistigh dá dtéamaí.",
        "profile_rationale_themes": "Ag tosú ó {preset}; comhaireann {themes} níos mó.",
        "profile_rationale_less": "Ag tosú ó {preset}; comhaireann {themes} níos lú.",
        "profile_rationale_indicators": "Ag tosú ó {preset}; tá níos mó meáchain ag "
        "{indicators} laistigh dá dtéamaí.",
        "profile_rationale_plain": "Níor mheaitseáil aon rud sa chur síos téama ná tomhas, mar "
        "sin tosaíonn sé ó {preset}.",
    },
}


def message(key: str, locale: str = "en", **values: str) -> str:
    """A fixed assistant message in ``locale``, English where no translation exists."""
    texts = MESSAGES.get(locale) or MESSAGES["en"]
    text = texts.get(key) or MESSAGES["en"][key]
    still = texts.get("still_works") or MESSAGES["en"]["still_works"]
    return text.format(still_works=still, **values)


def language_instruction(locale: str) -> str | None:
    """The line added to the prompt when the page is not in English."""
    if locale in ("en", None) or locale not in LANGUAGE_NAMES:
        return None
    name = LANGUAGE_NAMES[locale]
    return (
        f"Reply in {name}. Keep place names as the user wrote them. If you cannot write "
        f"{name} well, say so in {name} and continue in English."
    )
