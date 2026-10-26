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
        "budget": "The assistant has used today's budget, so it's resting until tomorrow "
        "(midnight UTC). {still_works}",
        "rate": "The assistant is busy right now; please try again in a minute. {still_works}",
        "session_limit": "This conversation has reached its limit of {turns} questions. Reload "
        "the page "
        "to start a new one. {still_works}",
        "flag_imputed": "Crime figures here are estimated: Greater Manchester Police publishes no "
        "street-level data.",
        "flag_low_n": "Few observations behind some values here (home sales, or a small Garda "
        "station's counts), so they are steadied or read with care.",
        "flag_broadcast_lad": "Secondary school quality comes from the council's exam results, "
        "because this "
        "nation publishes no per-school results or inspection grades.",
        "flag_not_available": "Some indicators are not available in this nation yet; the theme "
        "uses the ones "
        "it has.",
        "flag_theme_gap": "This nation publishes too little open data for {themes} to be scored: "
        "shown as "
        "limited data and left out of the overall.",
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
        # Singular forms, picked when one theme or one measure is named (#161)
        "profile_rationale_one_theme": "Starts from {preset}; {themes} counts more, and "
        "{indicators} weigh more within their themes.",
        "profile_rationale_one_indicator": "Starts from {preset}; {themes} count more, and "
        "{indicators} weighs more within its theme.",
        "profile_rationale_one_theme_one_indicator": "Starts from {preset}; {themes} counts "
        "more, and {indicators} weighs more within its theme.",
        "profile_rationale_themes_one_theme": "Starts from {preset}; {themes} counts more.",
        "profile_rationale_less_one_theme": "Starts from {preset}; {themes} counts less.",
        "profile_rationale_indicators_one_indicator": "Starts from {preset}; {indicators} "
        "weighs more within its theme.",
    },
    "cy": {
        "still_works": "Mae'r map, y pwysau a thudalennau'r ardaloedd yn dal i weithio.",
        "budget": "Mae'r cynorthwyydd wedi defnyddio cyllideb heddiw, felly mae'n gorffwys tan "
        "yfory (hanner nos UTC). {still_works}",
        "rate": "Mae'r cynorthwyydd yn brysur ar hyn o bryd; rhowch gynnig arall arni mewn "
        "munud. {still_works}",
        "session_limit": "Mae'r sgwrs hon wedi cyrraedd ei therfyn o {turns} cwestiwn. "
        "Ail-lwythwch y "
        "dudalen i ddechrau un newydd. {still_works}",
        "flag_imputed": "Amcangyfrif yw'r ffigurau trosedd yma: nid yw Heddlu Manceinion Fwyaf yn "
        "cyhoeddi data lefel stryd.",
        "flag_low_n": "Ychydig o arsylwadau sydd y tu ôl i rai gwerthoedd yma (gwerthiannau tai, "
        "neu "
        "gyfrif gorsaf Garda fach), felly cânt eu sefydlogi neu eu darllen yn ofalus.",
        "flag_broadcast_lad": "Daw ansawdd ysgolion uwchradd o ganlyniadau arholiadau'r cyngor, "
        "oherwydd nid "
        "yw'r genedl hon yn cyhoeddi canlyniadau na graddau arolygu fesul ysgol.",
        "flag_not_available": "Nid yw rhai dangosyddion ar gael yn y genedl hon eto; mae'r thema'n "
        "defnyddio'r rhai sydd ganddi.",
        "flag_theme_gap": "Mae'r genedl hon yn cyhoeddi rhy ychydig o ddata agored i sgorio "
        "{themes}: "
        "fe'i dangosir fel data cyfyngedig a'i adael allan o'r cyfanswm.",
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
        "budget": "Chleachd an cuidiche buidseat an latha an-diugh, mar sin tha e a' gabhail fois "
        "gu màireach (meadhan-oidhche UTC). {still_works}",
        "rate": "Tha an cuidiche trang an-dràsta; feuch a-rithist ann am mionaid. {still_works}",
        "session_limit": "Ràinig an còmhradh seo a chrìoch de {turns} ceistean. Ath-luchdaich an "
        "duilleag airson fear ùr a thòiseachadh. {still_works}",
        "flag_imputed": "Tha na figearan eucoir an seo nan tuairmse: chan fhoillsich Poileas "
        "Manchester "
        "Mhòr dàta aig ìre sràide.",
        "flag_low_n": "Chan eil ach beagan bheachdan air cùl cuid de na luachan an seo (reic "
        "thaighean, no cunntasan stèisein Garda bhig), mar sin tha iad air an "
        "socrachadh no air an leughadh le cùram.",
        "flag_broadcast_lad": "Tha càileachd nan àrd-sgoiltean a' tighinn bho thoraidhean "
        "deuchainn na "
        "comhairle, oir chan fhoillsich an nàisean seo toraidhean no ìrean sgrùdaidh "
        "airson gach sgoil.",
        "flag_not_available": "Chan eil cuid de na comharran rim faighinn san nàisean seo "
        "fhathast; "
        "cleachdaidh an cuspair an fheadhainn a th' aige.",
        "flag_theme_gap": "Tha an nàisean seo a' foillseachadh ro bheag de dhàta fosgailte airson "
        "{themes} a sgòradh: air a shealltainn mar dhàta cuingealaichte agus air "
        "fhàgail a-mach às an iomlan.",
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
        "budget": "D'úsáid an cúntóir buiséad an lae inniu, mar sin tá sé ag glacadh sosa go dtí "
        "amárach (meán oíche UTC). {still_works}",
        "rate": "Tá an cúntóir gnóthach faoi láthair; bain triail eile as i gceann nóiméid. "
        "{still_works}",
        "session_limit": "Tá teorainn {turns} ceist an chomhrá seo sroichte. Athlódáil an "
        "leathanach "
        "chun ceann nua a thosú. {still_works}",
        "flag_imputed": "Is meastacháin iad na figiúirí coireachta anseo: ní fhoilsíonn Póilíní "
        "Mhanchain Mhóir sonraí ar leibhéal na sráide.",
        "flag_low_n": "Is beag breathnóireacht atá taobh thiar de roinnt luachanna anseo "
        "(díolacháin "
        "tithe, nó comhaireamh stáisiúin Garda bhig), mar sin déantar iad a chobhsú nó "
        "a léamh go cúramach.",
        "flag_broadcast_lad": "Tagann caighdeán na meánscoileanna ó thorthaí scrúduithe na "
        "comhairle, toisc "
        "nach bhfoilsíonn an náisiún seo torthaí ná gráid chigireachta in aghaidh na "
        "scoile.",
        "flag_not_available": "Níl roinnt táscairí ar fáil sa náisiún seo go fóill; úsáideann an "
        "téama na "
        "cinn atá aige.",
        "flag_theme_gap": "Foilsíonn an náisiún seo rófhuar sonraí oscailte chun {themes} a "
        "scóráil: "
        "taispeántar é mar shonraí teoranta agus fágtar amach as an iomlán é.",
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


def message(key: str, locale: str = "en", fallback: str | None = None, **values: str) -> str:
    """A fixed assistant message in ``locale``, English where no translation exists; a
    ``fallback`` key stands in where neither language has ``key`` (a singular form of a
    template that only some languages need)."""
    texts = MESSAGES.get(locale) or MESSAGES["en"]
    # The language's own plain form beats the English singular one
    text = texts.get(key) or (fallback and texts.get(fallback)) or MESSAGES["en"].get(key)
    if text is None and fallback:
        text = MESSAGES["en"][fallback]
    if text is None:
        raise KeyError(key)
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
