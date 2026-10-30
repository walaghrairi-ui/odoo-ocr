from odoo import models, api, fields
from odoo.exceptions import UserError
import json
import re
from datetime import datetime, timedelta


# =====================================================================
#  Modele Groq. Le token, lui, se configure dans
#  Parametres > Comptabilite > Saisie automatique IA (Groq).
# =====================================================================
GROQ_MODEL = "openai/gpt-oss-120b"


def _fmt(montant):
    """Formate 8014.65 -> '8 014,65' (espaces milliers, virgule decimale)."""
    s = f"{montant:,.2f}".replace(",", " ").replace(".", ",")
    if s.endswith(",00"):
        s = s[:-3]
    return s


class InvoiceAgent(models.AbstractModel):
    """Agent IA universel : repond a toute question sur les factures,
    tickets, fournisseurs et comptes comptables en interrogeant Odoo."""
    _name = 'invoice.ocr.agent'
    _description = 'Agent IA pour les factures'

    # =================================================================
    #  POINT D'ENTREE
    # =================================================================
    @api.model
    def ask_agent(self, question, history=None):
        if not question or not question.strip():
            return {"answer": "Veuillez poser une question.", "table": None}

        try:
            # Etape 1 : l'IA genere un PLAN de recherche universel
            plan = self._generate_query_plan(question, history)
            type_demande = plan.get('type', 'data')

            # Hors sujet -> reponse polie
            if type_demande == 'hors_sujet':
                return {
                    "answer": (
                        "Je suis l'assistant dedie a vos factures, tickets et fournisseurs. "
                        "Je ne peux pas repondre a cette question, mais je peux vous aider "
                        "a analyser vos documents comptables.\n\n"
                        "Par exemple : \"Quels sont mes 5 plus gros fournisseurs ?\""
                    ),
                    "table": None,
                }

            # Question sur la conversation elle-meme -> reponse basee sur donnees reelles
            if type_demande == 'conversationnel':
                return self._handle_conversational(question, history)

            # Demande d'ACTION -> preparation + demande de confirmation
            if type_demande == 'action':
                return self._prepare_action(question, plan)

            # Etape 2 : execution du plan sur les VRAIES donnees Odoo
            resultat = self._execute_plan(plan)

            # Etape 3 : construction de la reponse (texte + tableau + graphique)
            return self._build_response(question, plan, resultat)

        except UserError as e:
            return {"answer": str(e), "table": None}
        except Exception:
            return {
                "answer": (
                    "Je n'ai pas pu traiter votre question pour le moment.\n\n"
                    "Verifiez votre connexion internet, puis reessayez ou "
                    "reformulez votre question."
                ),
                "table": None,
            }

    # =================================================================
    #  ETAPE 1 : L'IA GENERE UN PLAN DE RECHERCHE UNIVERSEL
    # =================================================================
    def _generate_query_plan(self, question, history=None):
        client = self.env['invoice.ocr.groq'].get_client()

        today = datetime.today().strftime('%Y-%m-%d')

        # Contexte des echanges precedents (memoire)
        contexte_historique = ""
        if history:
            lignes = []
            for h in history:
                role = "Utilisateur" if h.get('role') == 'user' else "Assistant"
                texte = (h.get('text') or '').strip()
                if texte:
                    lignes.append(f"{role} : {texte}")
            if lignes:
                contexte_historique = (
                    "\nEchanges precedents (a utiliser si la question y fait "
                    "reference, ex: 'et en 2026 ?') :\n" + "\n".join(lignes) + "\n"
                )

        prompt = f"""Tu es le cerveau d'un assistant comptable connecte a une base Odoo.
Tu transformes la question de l'utilisateur en un PLAN de recherche structure.

Date d'aujourd'hui : {today}
{contexte_historique}
Question : "{question}"

DONNEES DISPONIBLES dans la base :
- Documents : factures fournisseurs et tickets de caisse
- Champs par document : fournisseur (nom), date, date d'echeance, montant total,
  type (facture/ticket), lignes avec compte comptable (code + libelle)

OPERATIONS POSSIBLES (combine-les librement selon la question) :
- Filtrer : par fournisseur, type de document, ETAT du document
  (brouillon / comptabilise), periode de dates, echeances proches,
  lignes sans compte comptable
- Grouper par : "partner" (fournisseur), "account" (compte comptable),
  "month" (mois), "year" (annee), ou null (pas de groupement)
- Agreger : "sum" (somme), "count" (nombre), "avg" (moyenne),
  "max" (document le plus eleve), "min" (document le plus bas)
- Trier : "desc" ou "asc" ; Limiter : un nombre (ex: 5 pour un top 5)
- Comparer plusieurs periodes (remplis "periods" avec 2+ periodes)
- Detecter les doublons : aggregate "duplicates". Retrouve les documents
  qui se repetent (meme fournisseur avec meme reference, ou meme
  fournisseur + meme montant + meme date).

Reponds UNIQUEMENT en JSON :
{{
    "type": "data" | "action" | "conversationnel" | "hors_sujet",
    "action": "validate" | "plan_payments" | null,
    "title": "titre court du resultat (ex: Top 5 fournisseurs)",
    "filters": {{
        "partner_name": "nom du fournisseur ou vide",
        "document_type": "facture" | "ticket" | "tous",
        "state": "brouillon" | "comptabilise" | "tous",
        "due_soon": true | false,
        "missing_account": true | false,
        "due_within_days": nombre de jours d'echeances a examiner ou null
    }},
    "periods": [
        {{"label": "2025", "date_from": "2025-01-01", "date_to": "2025-12-31"}}
    ],
    "operation": {{
        "group_by": "partner" | "account" | "month" | "year" | null,
        "aggregate": "sum" | "count" | "avg" | "max" | "min" | "duplicates",
        "sort": "desc" | "asc",
        "limit": null
    }},
    "output": {{
        "show_kpis": true | false,
        "show_table": true | false,
        "show_chart": true | false,
        "chart_type": "bar" | "pie" | "line" | null
    }}
}}

REGLES DE PLANIFICATION :
- "hors_sujet" UNIQUEMENT si la question n'a rien a voir avec les factures,
  tickets, fournisseurs, depenses ou comptes (ex: meteo, capitale, sport).
- "conversationnel" si l'utilisateur pose une question SUR la discussion
  (ex: "de quel client parle-t-on ?", "explique ta reponse", "repete").
- Le filtre "state" traduit l'etat comptable du document :
  "brouillon" quand l'utilisateur parle de documents non valides
  (brouillon, non comptabilise, en attente, a valider) ;
  "comptabilise" pour les documents deja valides (comptabilise, poste,
  confirme, valide) ; "tous" sinon. Exemple : "combien de factures en
  brouillon ?" -> type "data", state "brouillon", aggregate "count".
- "action" si l'utilisateur DEMANDE D'AGIR sur les documents au lieu de
  simplement consulter. Deux actions existent :

  * "validate" : valider, comptabiliser, confirmer, poster des documents
    en brouillon. L'etat vise est "brouillon".
    Exemples : "valide les factures brouillon de TechnoPro" -> action
    "validate", partner_name "TechnoPro" ; "comptabilise tous mes tickets
    en brouillon" -> action "validate", document_type "ticket".

  * "plan_payments" : organiser, planifier ou programmer le PAIEMENT des
    factures, poser des rappels d'echeance, preparer un echeancier ou un
    planning de tresorerie. L'etat vise est "comptabilise" (on ne paie que
    ce qui est deja comptabilise). Renseigne "due_within_days" d'apres la
    periode citee : 7 pour une semaine, 15 pour une quinzaine, 30 pour un
    mois, null si rien n'est precise.
    Exemples : "organise mes paiements de la semaine" -> action
    "plan_payments", state "comptabilise", due_within_days 7 ;
    "planifie mes paiements du mois" -> due_within_days 30 ;
    "mets-moi des rappels pour les factures a payer" -> due_within_days
    null ; "prepare mon planning de paiement pour TechnoPro" ->
    partner_name "TechnoPro" ; "rappelle-moi de payer mes factures qui
    arrivent a echeance" -> action "plan_payments".

  ATTENTION : une simple consultation reste une QUESTION (type "data"),
  jamais une action. "combien de factures en brouillon ?" et "quelles
  factures arrivent a echeance ?" sont des questions : on repond, on
  n'ecrit rien dans Odoo.
- Sinon "type": "data" et construis le plan.
- Si la question fait reference au contexte precedent, reprends le fournisseur
  ou la periode des echanges precedents.
- "periods" vide [] si aucune periode precise n'est demandee.

REGLES DE PRESENTATION (TRES IMPORTANT) :
Ta reponse doit toujours contenir un court paragraphe. A cela s'ajoute
AU PLUS UN SEUL support visuel, jamais deux. Un bon analyste ne montre
jamais la meme information sous deux formes differentes.

* show_table et show_chart ne doivent JAMAIS etre vrais en meme temps.

* show_chart = true quand une visualisation aide a saisir des grandeurs :
  un classement, une repartition, une evolution, une comparaison.
  C'est le choix a privilegier des qu'il y a peu d'elements (2 a 8).

* show_table = true uniquement pour une longue liste d'elements
  (plus de 8 lignes) ou quand seule la valeur exacte compte, sans
  comparaison visuelle utile.

* Si la reponse tient en un seul chiffre ou un seul document :
  show_table = false, show_chart = false. Le paragraphe suffit.

* show_kpis = true uniquement pour une COMPARAISON entre periodes :
  une carte affichera l'evolution en pourcentage. Dans tous les autres
  cas, mets false.

* chart_type :
  - "pie"  pour une REPARTITION (parts d'un tout)
  - "line" pour une EVOLUTION dans le temps (par mois, par annee)
  - "bar"  pour une COMPARAISON ou un CLASSEMENT
  - null si show_chart est false

EXEMPLES DE DECISIONS :
  * "combien de factures ce mois-ci" -> un seul nombre
    -> aggregate "count", show_kpis false, show_table false, show_chart false
  * "ma facture la plus chere" -> un seul document
    -> aggregate "max", show_kpis false, show_table false, show_chart false
  * "moyenne de mes factures" -> un seul chiffre
    -> aggregate "avg", tout a false
  * "combien j'ai depense chez Dell" -> un montant
    -> aggregate "sum", tout a false
  * "mes 5 plus gros fournisseurs" -> un classement court
    -> group_by "partner", aggregate "sum", sort "desc", limit 5,
       show_chart true, chart_type "bar", show_table false, show_kpis false
  * "repartition de mes depenses par compte comptable" -> des parts d'un tout
    -> group_by "account", aggregate "sum", show_chart true,
       chart_type "pie", show_table false, show_kpis false
  * "evolution de mes depenses par mois en 2026" -> une tendance
    -> group_by "month", aggregate "sum", sort "asc", show_chart true,
       chart_type "line", show_table false, show_kpis false
  * "compare mes factures de 2025 et 2026" -> comparaison de periodes
    -> periods avec les 2 annees, show_chart true, chart_type "bar",
       show_table false, show_kpis true
  * "liste toutes mes factures" -> longue liste
    -> show_table true, show_chart false, show_kpis false
  * "combien de factures en brouillon" -> comptage filtre par etat
    -> state "brouillon", aggregate "count", tous les visuels a false
  * "ai-je des factures en double" -> recherche de doublons
    -> group_by null, aggregate "duplicates", show_table true,
       show_chart false, show_kpis false

- Ne mets JAMAIS de texte hors du JSON.
"""

        text = ""
        for tentative in range(2):
            try:
                # response_format json_object : le modele est contraint de
                # renvoyer un JSON valide, sans texte autour.
                response = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                )
                text = response.choices[0].message.content.strip()
                break
            except Exception:
                # Repli : certains modeles n'acceptent pas response_format
                try:
                    response = client.chat.completions.create(
                        model=GROQ_MODEL,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    text = response.choices[0].message.content.strip()
                    break
                except Exception:
                    if tentative == 1:
                        raise
        # Retirer un eventuel bloc markdown ```json ... ```
        text = re.sub(r'^```(?:json)?|```$', '', text.strip(),
                      flags=re.MULTILINE).strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            text = match.group()

        try:
            plan = json.loads(text)
            if not isinstance(plan, dict):
                plan = {}
        except json.JSONDecodeError:
            plan = {}

        # Valeurs par defaut de securite (robuste meme si l'IA renvoie null)
        plan['type'] = plan.get('type') or 'data'
        plan['title'] = plan.get('title') or 'Resultat'
        plan['filters'] = plan.get('filters') or {}
        plan['periods'] = plan.get('periods') or []
        plan['operation'] = plan.get('operation') or {}
        plan['output'] = plan.get('output') or {}
        op = plan['operation']
        op['group_by'] = op.get('group_by') or None
        op['aggregate'] = op.get('aggregate') or 'count'
        op['sort'] = op.get('sort') or 'desc'
        op['limit'] = op.get('limit')
        # Presentation : par defaut on n'affiche RIEN de plus que le texte.
        # Un visuel doit toujours etre un choix explicite et justifie.
        out = plan['output']
        out['show_kpis'] = bool(out.get('show_kpis'))
        out['show_table'] = bool(out.get('show_table'))
        out['show_chart'] = bool(out.get('show_chart'))
        out['chart_type'] = out.get('chart_type') or 'bar'
        return plan

    # =================================================================
    #  ETAPE 2 : EXECUTEUR UNIVERSEL (vraies donnees Odoo)
    # =================================================================
    def _search_moves(self, filters, date_from=None, date_to=None):
        """Recherche les documents reels selon les filtres."""
        doc_type = (filters or {}).get('document_type', 'tous')
        if doc_type == 'facture':
            domain = [('move_type', '=', 'in_invoice')]
        elif doc_type == 'ticket':
            domain = [('move_type', '=', 'in_receipt')]
        else:
            domain = [('move_type', 'in', ['in_invoice', 'in_receipt'])]

        partner_name = ((filters or {}).get('partner_name') or '').strip()
        if partner_name:
            domain.append(('partner_id.name', 'ilike', partner_name))

        # Etat comptable : le filtre est applique dans la requete Odoo,
        # le comptage provient donc toujours de la base, jamais de l'IA.
        etat = ((filters or {}).get('state') or 'tous').strip().lower()
        if etat in ('brouillon', 'draft'):
            domain.append(('state', '=', 'draft'))
        elif etat in ('comptabilise', 'comptabilisee', 'posted', 'valide'):
            domain.append(('state', '=', 'posted'))

        if date_from:
            domain.append(('invoice_date', '>=', date_from))
        if date_to:
            domain.append(('invoice_date', '<=', date_to))

        if (filters or {}).get('due_soon'):
            today = datetime.today().date()
            in_seven = today + timedelta(days=7)
            domain.append(('invoice_date_due', '>=', today.strftime('%Y-%m-%d')))
            domain.append(('invoice_date_due', '<=', in_seven.strftime('%Y-%m-%d')))

        moves = self.env['account.move'].search(domain)

        if (filters or {}).get('missing_account'):
            moves = moves.filtered(
                lambda m: any(not l.account_id for l in m.invoice_line_ids)
            )
        return moves

    def _execute_plan(self, plan):
        """Execute le plan et retourne les resultats calcules sur les vraies donnees."""
        filters = plan.get('filters') or {}
        operation = plan.get('operation') or {}
        periods = plan.get('periods') or []

        group_by = operation.get('group_by')
        aggregate = operation.get('aggregate', 'count')
        sort_dir = operation.get('sort', 'desc')
        limit = operation.get('limit')

        # --- CAS A : comparaison entre plusieurs periodes -------------
        if len(periods) >= 2:
            rows = []
            for p in periods:
                moves = self._search_moves(filters, p.get('date_from'), p.get('date_to'))
                rows.append({
                    'label': p.get('label', ''),
                    'count': len(moves),
                    'total': round(sum(moves.mapped('amount_total')), 3),
                })
            return {'kind': 'comparison', 'rows': rows}

        # --- Periode unique eventuelle --------------------------------
        date_from = date_to = None
        if len(periods) == 1:
            date_from = periods[0].get('date_from')
            date_to = periods[0].get('date_to')

        moves = self._search_moves(filters, date_from, date_to)

        # --- CAS B : groupement (top fournisseurs, repartition...) -----
        if group_by:
            groups = {}
            if group_by == 'account':
                # Groupement par compte comptable des lignes
                for m in moves:
                    for line in m.invoice_line_ids:
                        if line.account_id:
                            key = f"{line.account_id.code} {line.account_id.name}"
                        else:
                            key = "Sans compte"
                        groups.setdefault(key, {'count': 0, 'total': 0.0})
                        groups[key]['count'] += 1
                        groups[key]['total'] += line.price_total
            else:
                for m in moves:
                    if group_by == 'partner':
                        key = m.partner_id.name or 'Inconnu'
                    elif group_by == 'month':
                        key = m.invoice_date.strftime('%m/%Y') if m.invoice_date else 'Sans date'
                    elif group_by == 'year':
                        key = str(m.invoice_date.year) if m.invoice_date else 'Sans date'
                    else:
                        key = 'Tous'
                    groups.setdefault(key, {'count': 0, 'total': 0.0})
                    groups[key]['count'] += 1
                    groups[key]['total'] += m.amount_total

            items = [
                {'key': k, 'count': v['count'], 'total': round(v['total'], 3)}
                for k, v in groups.items()
            ]
            sort_key = 'count' if aggregate == 'count' else 'total'
            items.sort(key=lambda x: x[sort_key], reverse=(sort_dir != 'asc'))
            if limit:
                items = items[:int(limit)]
            return {'kind': 'grouped', 'items': items, 'group_by': group_by,
                    'aggregate': aggregate}

        # --- CAS C bis : detection des doublons -------------------------
        if aggregate == 'duplicates':
            groupes = {}
            for m in moves:
                partenaire = m.partner_id.name or 'Inconnu'
                reference = (m.ref or '').strip()
                # Deux criteres de rapprochement :
                #  - meme fournisseur ET meme reference de facture
                #  - meme fournisseur, meme montant ET meme date
                if reference:
                    cle = ('ref', partenaire.lower(), reference.lower())
                else:
                    cle = ('mnt', partenaire.lower(),
                           round(m.amount_total, 2),
                           str(m.invoice_date or ''))
                groupes.setdefault(cle, []).append(m)

            doublons = []
            for cle, docs in groupes.items():
                if len(docs) < 2:
                    continue
                # Un montant nul ne constitue pas un indice fiable
                if cle[0] == 'mnt' and cle[2] <= 0:
                    continue
                premier = docs[0]
                doublons.append({
                    'fournisseur': premier.partner_id.name or 'Inconnu',
                    'reference': (premier.ref or '').strip() or '-',
                    'date': str(premier.invoice_date) if premier.invoice_date else '-',
                    'montant': round(premier.amount_total, 3),
                    'occurrences': len(docs),
                    'critere': ('Meme reference' if cle[0] == 'ref'
                                else 'Meme montant et meme date'),
                    'ids': docs.ids if hasattr(docs, 'ids') else [d.id for d in docs],
                })

            doublons.sort(key=lambda d: d['occurrences'], reverse=True)
            return {'kind': 'duplicates', 'items': doublons,
                    'total_documents': len(moves)}

        # --- CAS C : document max / min --------------------------------
        if aggregate in ('max', 'min'):
            if not moves:
                return {'kind': 'empty'}
            if aggregate == 'max':
                doc = max(moves, key=lambda m: m.amount_total)
            else:
                doc = min(moves, key=lambda m: m.amount_total)
            return {'kind': 'single_doc', 'doc': {
                'fournisseur': doc.partner_id.name or 'Inconnu',
                'date': str(doc.invoice_date) if doc.invoice_date else '',
                'montant': round(doc.amount_total, 3),
                'type': 'Ticket' if doc.move_type == 'in_receipt' else 'Facture',
            }}

        # --- CAS D : statistiques globales (somme, nombre, moyenne) ----
        count = len(moves)
        total = round(sum(moves.mapped('amount_total')), 3)
        avg = round(total / count, 3) if count else 0
        docs = []
        for m in moves[:10]:
            docs.append({
                'fournisseur': m.partner_id.name or 'Inconnu',
                'date': str(m.invoice_date) if m.invoice_date else '',
                'montant': round(m.amount_total, 3),
                'type': 'Ticket' if m.move_type == 'in_receipt' else 'Facture',
            })
        return {'kind': 'stats', 'count': count, 'total': total,
                'avg': avg, 'docs': docs, 'aggregate': aggregate}

    # =================================================================
    #  INTELLIGENCE DE PRESENTATION
    #  L'IA propose une forme de reponse, ces regles la valident.
    # =================================================================
    def _decide_presentation(self, plan, resultat):
        """Choisit LA forme de reponse la plus adaptee.

        Principe : une reponse professionnelle ne repete jamais la meme
        information sous plusieurs formes. On retient donc AU PLUS un seul
        support visuel, et un indicateur uniquement s'il apporte une
        information absente du texte (l'evolution en pourcentage).
        """
        output = plan.get('output') or {}
        kind = resultat.get('kind')

        veut_table = bool(output.get('show_table'))
        veut_chart = bool(output.get('show_chart'))
        chart_type = output.get('chart_type') or 'bar'

        aucun = {'kpis': False, 'table': False,
                 'chart': False, 'chart_type': None}

        # --- Doublons : un tableau, jamais de graphique ------------------
        if kind == 'duplicates':
            trouves = len(resultat.get('items') or [])
            return {'kpis': False, 'table': trouves > 0,
                    'chart': False, 'chart_type': None}

        # --- Reponses uniques : jamais de visuel -------------------------
        if kind in ('single_doc', 'empty'):
            return aucun
        if kind == 'stats':
            # La reponse tient dans une phrase
            return aucun

        # --- Nombre de lignes reellement disponibles ---------------------
        if kind == 'comparison':
            lignes = resultat.get('rows') or []
        else:
            lignes = resultat.get('items') or []
        nb = len(lignes)

        # Comparer suppose au moins deux elements
        if nb < 2:
            return aucun

        # --- Choix du support unique -------------------------------------
        # Au-dela de 8 lignes un graphique devient illisible : on privilegie
        # le tableau. En deca, le graphique suffit (les valeurs y figurent).
        if nb > 8:
            table, chart = True, False
            chart_type = None
        elif veut_chart:
            table, chart = False, True
        elif veut_table:
            table, chart = True, False
            chart_type = None
        else:
            # L'IA n'a rien demande : un classement reste plus parlant en
            # graphique, on le propose par defaut.
            table, chart = False, True

        # --- Coherence du type de graphique ------------------------------
        if chart:
            group_by = resultat.get('group_by')
            if group_by in ('month', 'year') and chart_type == 'bar':
                chart_type = 'line'
            if chart_type == 'pie':
                valeurs = [it.get('total', 0) for it in lignes]
                if nb > 8 or any(v <= 0 for v in valeurs):
                    chart_type = 'bar'

        # --- Indicateur : uniquement l'evolution d'une comparaison --------
        kpis = False
        if kind == 'comparison' and nb >= 2:
            premier = lignes[0].get('total', 0)
            if premier > 0:
                kpis = True

        return {'kpis': kpis, 'table': table,
                'chart': chart, 'chart_type': chart_type}

    # =================================================================
    #  ETAPE 3 : CONSTRUIRE LA REPONSE (texte + visuels si pertinents)
    # =================================================================
    def _build_response(self, question, plan, resultat):
        devise = self.env.company.currency_id.name or 'DT'
        titre = plan.get('title') or 'Resultat'
        kind = resultat.get('kind')

        # L'agent decide de la forme de sa reponse
        presentation = self._decide_presentation(plan, resultat)

        table = None
        chart = None
        kpis = None

        # ---------------- Comparaison de periodes ----------------
        if kind == 'comparison':
            rows = resultat['rows']
            if presentation['table']:
                table = {
                    "title": titre,
                    "currency": devise,
                    "headers": ["Periode", "Nombre de documents", "Montant total"],
                    "rows": [
                        [r['label'], str(r['count']), f"{_fmt(r['total'])} {devise}"]
                        for r in rows
                    ],
                }
            if presentation['chart']:
                chart = {
                    "title": titre,
                    "currency": devise,
                    "type": presentation['chart_type'],
                    "labels": [r['label'] for r in rows],
                    "amounts": [r['total'] for r in rows],
                    "counts": [r['count'] for r in rows],
                }
            if presentation['kpis']:
                evolution = None
                if len(rows) >= 2 and rows[0]['total'] > 0:
                    evolution = round(
                        ((rows[-1]['total'] - rows[0]['total']) / rows[0]['total']) * 100, 1)
                if evolution is not None:
                    # Seul indicateur conserve : l'evolution, car c'est la
                    # seule information que le texte ne donne pas directement.
                    kpis = {
                        "evolution": evolution,
                        "depart": rows[0].get('label', ''),
                        "arrivee": rows[-1].get('label', ''),
                        "devise": devise,
                        "solo": True,
                    }
            donnees = {'periodes': rows, 'devise': devise}

        # ---------------- Resultats groupes (top, repartition) ----
        elif kind == 'grouped':
            items = resultat['items']
            group_by = resultat['group_by']
            label_col = {
                'partner': 'Fournisseur',
                'account': 'Compte comptable',
                'month': 'Mois',
                'year': 'Annee',
            }.get(group_by, 'Groupe')

            if presentation['table']:
                table = {
                    "title": titre,
                    "currency": devise,
                    "headers": [label_col, "Nombre", "Montant total"],
                    "rows": [
                        [it['key'], str(it['count']), f"{_fmt(it['total'])} {devise}"]
                        for it in items
                    ],
                }
            if presentation['chart']:
                chart = {
                    "title": titre,
                    "currency": devise,
                    "type": presentation['chart_type'],
                    "labels": [it['key'] for it in items[:10]],
                    "amounts": [it['total'] for it in items[:10]],
                    "counts": [it['count'] for it in items[:10]],
                }
            donnees = {'groupes': items, 'devise': devise}

        # ---------------- Doublons detectes -----------------------
        elif kind == 'duplicates':
            items = resultat['items']
            if presentation['table'] and items:
                table = {
                    "title": "Doublons detectes",
                    "currency": devise,
                    "headers": ["Fournisseur", "Reference", "Date",
                                "Montant", "Occurrences", "Critere"],
                    "rows": [
                        [it['fournisseur'], it['reference'], it['date'],
                         f"{_fmt(it['montant'])} {devise}",
                         f"{it['occurrences']} fois", it['critere']]
                        for it in items
                    ],
                }
            donnees = {
                'doublons_trouves': len(items),
                'documents_analyses': resultat.get('total_documents', 0),
                'details': items[:10],
                'devise': devise,
            }

        # ---------------- Document max / min ----------------------
        elif kind == 'single_doc':
            donnees = {'document': resultat['doc'], 'devise': devise}

        # ---------------- Statistiques globales -------------------
        elif kind == 'stats':
            etat = ((plan.get('filters') or {}).get('state') or 'tous')
            donnees = {
                'nombre': resultat['count'],
                'total': resultat['total'],
                'moyenne': resultat['avg'],
                'documents': resultat['docs'],
                'devise': devise,
            }
            if etat and etat != 'tous':
                # Le filtre a ete applique dans la requete : on le precise
                # pour que la reponse soit sans ambiguite.
                donnees['etat_filtre'] = etat
            # Aucun indicateur : la reponse tient dans la phrase.

        else:  # empty
            donnees = {'nombre': 0, 'devise': devise}

        # Garde-fou general : si la recherche ne retourne rien, on verifie
        # si la base contient malgre tout des documents. Cela evite d'affirmer
        # "vous n'avez aucune facture" alors que seuls les criteres ne
        # correspondaient pas. S'applique a tous les types de resultats.
        if self._resultat_est_vide(donnees):
            total_base = self.env['account.move'].search_count(
                [('move_type', 'in', ['in_invoice', 'in_receipt'])]
            )
            if total_base > 0:
                donnees['documents_dans_la_base'] = total_base

        # L'IA redige la reponse finale a partir des VRAIES donnees
        answer = self._format_answer(question, donnees, bool(table or chart))
        return {"answer": answer, "table": table, "chart": chart, "kpis": kpis}

    def _resultat_est_vide(self, donnees):
        """Determine si la recherche n'a retourne aucun document."""
        # Une recherche de doublons qui n'en trouve aucun est un resultat
        # valide, pas une absence de donnees.
        if 'doublons_trouves' in donnees:
            return False
        if donnees.get('nombre') == 0:
            return True
        for cle in ('groupes', 'periodes', 'details'):
            if cle in donnees and not donnees[cle]:
                return True
        if 'periodes' in donnees:
            if all((p.get('count') or 0) == 0 for p in donnees['periodes']):
                return True
        return False

    def _format_answer(self, question, donnees, has_visuals=False):
        client = self.env['invoice.ocr.groq'].get_client()

        if has_visuals:
            note_visuels = (
                "\nCONTEXTE D'AFFICHAGE : un tableau et/ou un graphique elegants "
                "sont deja affiches sous ta reponse. Ne dessine donc RIEN toi-meme "
                "et ne repete pas tous les chiffres : donne l'essentiel et "
                "l'interpretation (tendance, element dominant, ecart notable)."
            )
        else:
            note_visuels = (
                "\nCONTEXTE D'AFFICHAGE : ta reponse s'affiche seule, sans tableau "
                "ni graphique. Elle doit donc etre complete et se suffire a "
                "elle-meme : donne directement le chiffre ou l'information "
                "demandee, en une ou deux phrases."
            )

        prompt = f"""Tu es un assistant comptable amical et professionnel.

Question : "{question}"

VRAIES donnees calculees depuis la base (utilise UNIQUEMENT ces donnees,
n'invente jamais de chiffre ni de nom) :
{json.dumps(donnees, ensure_ascii=False)}
{note_visuels}

Reponds en francais, clair, court et naturel (1 a 3 phrases maximum).
- Va droit au but : reponds a la question posee, sans remplissage
- Le champ "nombre" est un comptage exact issu de la base de donnees.
  Reprends-le tel quel, ne le recalcule jamais et n'affirme jamais que
  l'information est indisponible quand ce champ est present.
- Si "etat_filtre" est present, la recherche portait uniquement sur les
  documents dans cet etat : mentionne-le dans ta reponse (par exemple
  "en brouillon").
- Si les donnees contiennent "doublons_trouves" : c'est le resultat d'une
  recherche de doublons. Si ce nombre vaut 0, indique clairement qu'AUCUN
  doublon n'a ete detecte parmi les documents analyses. N'affirme jamais
  qu'il existe des doublons si ce nombre est nul.
- Utilise la devise indiquee
- Si un classement est present, cite seulement les premiers elements
- Si les donnees sont vides ou a zero, dis-le poliment
- Si "documents_dans_la_base" est present, cela signifie qu'AUCUN document
  ne correspond aux criteres recherches, alors que la base en contient
  d'autres. N'affirme JAMAIS dans ce cas que la base est vide ou qu'il
  n'y a aucune facture : precise qu'aucun document ne correspond a cette
  recherche precise, et invite l'utilisateur a reformuler ou preciser.
- INTERDIT ABSOLU : ne dessine JAMAIS de tableau en texte, jamais de
  caracteres | ou ---, jamais de markdown, jamais de liste a puces.
  Ecris uniquement des phrases naturelles.
- Jamais de JSON ni de terme technique
"""
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()

    # =================================================================
    #  ACTIONS : l'IA propose, l'utilisateur confirme, le code execute
    # =================================================================
    # Chaque action declare l'etat des documents sur lequel elle s'applique.
    # Ajouter une action revient a completer cette table puis a ecrire ses
    # deux methodes _prepare_<action> et _execute_<action> : la verification
    # de coherence et les garde-fous ci-dessous restent inchanges.
    ACTIONS_ETAT_REQUIS = {
        'validate': 'brouillon',
        'plan_payments': 'comptabilise',
    }

    # Privilege exige pour executer une action, quelle qu'elle soit.
    # Poser une question ne modifie rien : cet acces reste ouvert a tout
    # utilisateur interne. Executer une action ecrit dans la comptabilite :
    # ce privilege est alors obligatoire. Une action ajoutee plus tard est
    # protegee automatiquement, sans ligne de code supplementaire.
    GROUPE_ACTION = 'invoice_ocr_agent.group_agent_manager'
    MESSAGE_PRIVILEGE = "Vous n'avez pas le privilege requis pour cette action."

    def _peut_executer_action(self):
        """Vrai si l'utilisateur courant a le privilege d'agir."""
        return self.env.user.has_group(self.GROUPE_ACTION)

    def _prepare_action(self, question, plan):
        """Prepare une action et demande confirmation. N'execute RIEN."""
        # Controle a l'affichage : sans privilege, aucune carte d'action
        # n'est proposee. L'utilisateur conserve tout le reste de l'assistant.
        if not self._peut_executer_action():
            return {"answer": self.MESSAGE_PRIVILEGE, "table": None}

        action = plan.get('action') or 'validate'
        filters = dict(plan.get('filters') or {})
        periods = plan.get('periods') or []

        etat_requis = self.ACTIONS_ETAT_REQUIS.get(action)
        preparateur = getattr(self, '_prepare_%s' % action, None)
        if etat_requis is None or preparateur is None:
            return {"answer": "Cette action n'est pas disponible pour le moment.",
                    "table": None}

        # Regle generale : une action ne modifie jamais silencieusement la
        # demande. Si l'utilisateur vise un etat incompatible avec l'action,
        # on l'explique au lieu de lui substituer d'autres documents.
        etat_demande = (filters.get('state') or 'tous').strip().lower()
        if etat_demande not in ('tous', etat_requis):
            return {
                "answer": (
                    f"Cette action ne s'applique qu'aux documents en "
                    f"{etat_requis}. Les documents en {etat_demande} ne "
                    "peuvent donc pas etre traites de cette maniere."
                ),
                "table": None,
            }

        date_from = date_to = None
        if len(periods) == 1:
            date_from = periods[0].get('date_from')
            date_to = periods[0].get('date_to')

        # L'etat requis est applique dans la requete elle-meme.
        filters['state'] = etat_requis
        return preparateur(filters, date_from, date_to)

    # -----------------------------------------------------------------
    #  ACTION 1 : valider (comptabiliser) des documents en brouillon
    # -----------------------------------------------------------------
    def _prepare_validate(self, filters, date_from, date_to):
        devise = self.env.company.currency_id.name or 'DT'
        brouillons = self._search_moves(filters, date_from, date_to)

        if not brouillons:
            return {
                "answer": ("Aucun document en brouillon ne correspond a votre "
                           "demande. Il n'y a donc rien a valider."),
                "table": None,
            }

        total = round(sum(brouillons.mapped('amount_total')), 3)

        # Liste selectionnable : chaque document est identifie
        documents = [{
            "id": m.id,
            "fournisseur": m.partner_id.name or 'Inconnu',
            "date": str(m.invoice_date) if m.invoice_date else '-',
            "type": 'Ticket' if m.move_type == 'in_receipt' else 'Facture',
            "montant": _fmt(m.amount_total),
            "montant_brut": round(m.amount_total, 3),
        } for m in brouillons]

        nb = len(brouillons)
        answer = (
            f"J'ai trouve {nb} document{'s' if nb > 1 else ''} en brouillon "
            f"pour un total de {_fmt(total)} {devise}. "
            "Selectionnez ceux que vous souhaitez valider, puis confirmez."
        )

        return {
            "answer": answer,
            "table": None,
            "chart": None,
            "kpis": None,
            "pending_action": {
                "type": "validate",
                "documents": documents,
                "count": nb,
                "devise": devise,
                "titre": "Selectionnez les documents a valider",
                "icone": "fa-shield",
                "total_label": "Total",
                "verbe": "Valider",
                "nom_item": "document",
                "vide_label": "Aucun document selectionne",
                "texte_encours": "Validation en cours...",
                "texte_annule": (
                    "Tres bien, aucune modification n'a ete effectuee. "
                    "Les documents restent en brouillon."
                ),
            },
        }

    @api.model
    def execute_action(self, action_type, move_ids):
        """Execute reellement l'action, uniquement apres confirmation."""
        # Controle cote serveur : c'est le seul qui protege reellement.
        # Masquer un bouton n'empeche rien ; un appel direct a cette methode,
        # sans passer par l'interface, est refuse ici.
        if not self._peut_executer_action():
            return {"success": False, "message": self.MESSAGE_PRIVILEGE}

        executeur = getattr(self, '_execute_%s' % action_type, None)
        if action_type not in self.ACTIONS_ETAT_REQUIS or executeur is None:
            return {"success": False, "message": "Action inconnue."}

        moves = self.env['account.move'].browse(move_ids or [])
        # Garde-fou commun : uniquement des documents fournisseurs existants.
        # Chaque action verifie ensuite l'etat qui lui est propre.
        moves = moves.filtered(
            lambda m: m.exists()
            and m.move_type in ('in_invoice', 'in_receipt')
        )
        return executeur(moves)

    def _execute_validate(self, moves):
        # L'etat est revalide ici : un document comptabilise entre-temps
        # par un autre utilisateur est ignore sans faire echouer le reste.
        moves = moves.filtered(lambda m: m.state == 'draft')

        if not moves:
            return {"success": False,
                    "message": ("Aucun document a valider : ils ont peut-etre "
                                "deja ete comptabilises entre-temps.")}

        valides = 0
        echecs = []
        for m in moves:
            try:
                m.action_post()
                valides += 1
            except Exception:
                echecs.append(m.partner_id.name or f"Document {m.id}")

        if valides and not echecs:
            message = (f"{valides} document{'s' if valides > 1 else ''} "
                       f"valide{'s' if valides > 1 else ''} avec succes. "
                       "Ils sont desormais comptabilises dans Odoo.")
        elif valides and echecs:
            message = (f"{valides} document(s) valide(s). "
                       f"{len(echecs)} n'ont pas pu l'etre "
                       f"({', '.join(echecs[:3])}) : verifiez leurs comptes "
                       "comptables ou les informations manquantes.")
        else:
            message = ("Aucun document n'a pu etre valide. Verifiez que les "
                       "lignes possedent bien un compte comptable et que "
                       "toutes les informations obligatoires sont renseignees.")

        return {"success": valides > 0, "validated": valides,
                "failed": len(echecs), "message": message}

    # -----------------------------------------------------------------
    #  ACTION 2 : organiser les paiements (rappels planifies dans Odoo)
    # -----------------------------------------------------------------
    # C'est l'agent qui decide : il lit les echeances, calcule l'urgence,
    # choisit la date de chaque rappel et totalise la sortie de tresorerie.
    RAPPEL_JOURS_AVANT = 2        # le rappel tombe 2 jours avant l'echeance
    RAPPEL_HORIZON_DEFAUT = 30    # on regarde 30 jours en avant par defaut
    RAPPEL_MARQUEUR = "Paiement a prevoir"   # evite tout rappel en double

    # Paliers d'urgence, evalues dans l'ordre : le premier dont la borne
    # superieure couvre le delai l'emporte. Ajouter un palier ne demande
    # aucune modification du reste du code.
    PALIERS_URGENCE = [
        {'cle': 'retard',  'max_jours': -1,   'label': 'En retard'},
        {'cle': 'urgent',  'max_jours': 2,    'label': 'Urgent'},
        {'cle': 'semaine', 'max_jours': 7,    'label': 'Cette semaine'},
        {'cle': 'avenir',  'max_jours': None, 'label': 'A venir'},
    ]

    def _palier_urgence(self, jours):
        for palier in self.PALIERS_URGENCE:
            borne = palier['max_jours']
            if borne is None or jours <= borne:
                return palier
        return self.PALIERS_URGENCE[-1]

    def _horizon_jours(self, filters):
        """Nombre de jours d'echeances a examiner (7 pour une semaine, etc.)."""
        try:
            jours = int((filters or {}).get('due_within_days'))
        except (TypeError, ValueError):
            jours = self.RAPPEL_HORIZON_DEFAUT
        if jours <= 0:
            jours = self.RAPPEL_HORIZON_DEFAUT
        return min(jours, 365)

    def _date_rappel(self, echeance):
        """Le rappel precede l'echeance, mais jamais dans le passe."""
        aujourdhui = fields.Date.context_today(self)
        rappel = echeance - timedelta(days=self.RAPPEL_JOURS_AVANT)
        return rappel if rappel > aujourdhui else aujourdhui

    def _libelle_delai(self, jours):
        if jours < 0:
            n = abs(jours)
            return f"echue depuis {n} jour{'s' if n > 1 else ''}"
        if jours == 0:
            return "echeance aujourd'hui"
        if jours == 1:
            return "echeance demain"
        return f"dans {jours} jours"

    def _a_deja_un_rappel(self, move):
        """Un rappel pose par l'agent existe-t-il deja sur ce document ?"""
        return any(
            a.summary and a.summary.startswith(self.RAPPEL_MARQUEUR)
            for a in move.activity_ids
        )

    def _prepare_plan_payments(self, filters, date_from, date_to):
        """Analyse les echeances et propose un calendrier de rappels.

        La periode citee par l'utilisateur porte ici sur les ECHEANCES et non
        sur les dates de facture : on raisonne donc sur un horizon en jours
        plutot que sur date_from / date_to.
        """
        devise = self.env.company.currency_id.name or 'DT'
        filters = dict(filters or {})
        filters.pop('due_soon', None)      # l'horizon joue deja ce role
        horizon = self._horizon_jours(filters)

        aujourdhui = fields.Date.context_today(self)
        limite = aujourdhui + timedelta(days=horizon)

        moves = self._search_moves(filters)
        # Reste a payer : le montant residuel est la source de verite.
        a_payer = moves.filtered(
            lambda m: m.amount_residual > 0.001
            and m.invoice_date_due
            and m.invoice_date_due <= limite
        )

        if not a_payer:
            return {
                "answer": (
                    f"Aucune facture comptabilisee ne reste a payer dans les "
                    f"{horizon} prochains jours. Votre echeancier est a jour."
                ),
                "table": None,
            }

        # On n'ajoute jamais un rappel sur un document qui en a deja un.
        deja = a_payer.filtered(lambda m: self._a_deja_un_rappel(m))
        a_planifier = a_payer - deja

        if not a_planifier:
            n = len(deja)
            return {
                "answer": (
                    f"Les {n} facture{'s' if n > 1 else ''} a payer dans les "
                    f"{horizon} prochains jours ont deja un rappel planifie. "
                    "Il n'y a rien a ajouter."
                ),
                "table": None,
            }

        documents = []
        groupes = {}
        total = 0.0
        # Tri par echeance : le plus urgent se presente en premier.
        for m in a_planifier.sorted(key=lambda d: d.invoice_date_due):
            jours = (m.invoice_date_due - aujourdhui).days
            palier = self._palier_urgence(jours)
            rappel = self._date_rappel(m.invoice_date_due)
            doc = {
                "id": m.id,
                "fournisseur": m.partner_id.name or 'Inconnu',
                "type": 'Ticket' if m.move_type == 'in_receipt' else 'Facture',
                "reference": m.name or '',
                "echeance": m.invoice_date_due.strftime('%d/%m/%Y'),
                "delai": self._libelle_delai(jours),
                "rappel": ("aujourd'hui" if rappel == aujourdhui
                           else rappel.strftime('%d/%m/%Y')),
                "montant": _fmt(m.amount_residual),
                "montant_brut": round(m.amount_residual, 3),
                "ton": palier['cle'],
            }
            documents.append(doc)
            total += m.amount_residual
            groupes.setdefault(palier['cle'], {
                "cle": palier['cle'],
                "label": palier['label'],
                "documents": [],
            })["documents"].append(doc)

        # Restitution dans l'ordre des paliers, du plus urgent au plus lointain.
        blocs = []
        for palier in self.PALIERS_URGENCE:
            bloc = groupes.get(palier['cle'])
            if not bloc:
                continue
            n = len(bloc['documents'])
            bloc['compte'] = f"{n} facture{'s' if n > 1 else ''}"
            bloc['montant'] = _fmt(
                sum(d['montant_brut'] for d in bloc['documents']))
            blocs.append(bloc)

        nb = len(documents)
        phrases = [
            f"J'ai examine vos echeances sur les {horizon} prochains jours : "
            f"{nb} facture{'s' if nb > 1 else ''} reste"
            f"{'nt' if nb > 1 else ''} a payer, pour un total de "
            f"{_fmt(total)} {devise}."
        ]
        if deja:
            d = len(deja)
            phrases.append(
                f"{d} facture{'s' if d > 1 else ''} possede"
                f"{'nt' if d > 1 else ''} deja un rappel, "
                f"je l'ai ecartee." if d == 1 else
                f"{d} factures possedent deja un rappel, je les ai ecartees."
            )
        phrases.append(
            f"Je propose un rappel {self.RAPPEL_JOURS_AVANT} jours avant chaque "
            "echeance. Selectionnez celles a planifier, puis confirmez."
        )

        return {
            "answer": " ".join(phrases),
            "table": None,
            "chart": None,
            "kpis": None,
            "pending_action": {
                "type": "plan_payments",
                "documents": documents,
                "groupes": blocs,
                "count": nb,
                "devise": devise,
                "titre": "Calendrier de paiement propose",
                "icone": "fa-calendar-check-o",
                "total_label": "Total a decaisser",
                "verbe": "Planifier",
                "nom_item": "rappel",
                "vide_label": "Aucune facture selectionnee",
                "texte_encours": "Planification des rappels...",
                "texte_annule": (
                    "Tres bien, aucun rappel n'a ete planifie. "
                    "Vos factures restent inchangees."
                ),
            },
        }

    def _execute_plan_payments(self, moves):
        # Etat revalide a l'execution : une facture reglee entre-temps
        # est ignoree sans faire echouer les autres.
        moves = moves.filtered(
            lambda m: m.state == 'posted'
            and m.amount_residual > 0.001
            and m.invoice_date_due
        )

        if not moves:
            return {"success": False, "planned": 0, "skipped": 0, "failed": 0,
                    "message": ("Aucune facture a planifier : elles ont "
                                "peut-etre deja ete reglees entre-temps.")}

        devise = self.env.company.currency_id.name or 'DT'
        planifies = 0
        ignores = 0
        echecs = []
        total = 0.0
        for m in moves:
            if self._a_deja_un_rappel(m):
                ignores += 1
                continue
            try:
                self._creer_rappel_paiement(m)
                planifies += 1
                total += m.amount_residual
            except Exception:
                echecs.append(m.partner_id.name or f"Document {m.id}")

        if planifies and not echecs:
            message = (
                f"{planifies} rappel{'s' if planifies > 1 else ''} "
                f"planifie{'s' if planifies > 1 else ''} dans Odoo, "
                f"pour un total de {_fmt(total)} {devise} a decaisser. "
                "Vous les retrouvez dans vos activites et dans le calendrier."
            )
        elif planifies and echecs:
            message = (
                f"{planifies} rappel(s) planifie(s). {len(echecs)} n'ont pas "
                f"pu l'etre ({', '.join(echecs[:3])})."
            )
        elif ignores:
            message = ("Ces factures avaient deja un rappel planifie. "
                       "Aucun doublon n'a ete cree.")
        else:
            message = ("Aucun rappel n'a pu etre planifie. Verifiez que les "
                       "factures possedent bien une date d'echeance.")

        return {"success": planifies > 0, "planned": planifies,
                "skipped": ignores, "failed": len(echecs), "message": message}

    def _creer_rappel_paiement(self, move):
        """Cree une activite planifiee Odoo, visible dans le calendrier."""
        echeance = move.invoice_date_due
        rappel = self._date_rappel(echeance)
        devise = self.env.company.currency_id.name or 'DT'
        resume = f"{self.RAPPEL_MARQUEUR} : {move.partner_id.name or ''}".strip()
        note = (
            "Rappel genere par l'assistant IA."
            f"<br/>Fournisseur : {move.partner_id.name or '-'}"
            f"<br/>Echeance : {echeance.strftime('%d/%m/%Y')}"
            f"<br/>Reste a payer : {_fmt(move.amount_residual)} {devise}"
        )
        try:
            move.activity_schedule(
                'mail.mail_activity_data_todo',
                date_deadline=rappel,
                summary=resume,
                note=note,
                user_id=self.env.uid,
            )
        except Exception:
            # Repli si le type d'activite standard est absent de la base
            type_act = self.env['mail.activity.type'].search([], limit=1)
            self.env['mail.activity'].create({
                'res_model_id': self.env['ir.model']._get_id('account.move'),
                'res_id': move.id,
                'activity_type_id': type_act.id if type_act else False,
                'summary': resume,
                'note': note,
                'date_deadline': rappel,
                'user_id': self.env.uid,
            })

    # =================================================================
    #  QUESTIONS CONVERSATIONNELLES (memoire basee sur vraies donnees)
    # =================================================================
    def _handle_conversational(self, question, history):
        client = self.env['invoice.ocr.groq'].get_client()

        lignes = []
        if history:
            for h in history:
                role = "Utilisateur" if h.get('role') == 'user' else "Assistant"
                texte = (h.get('text') or '').strip()
                if texte:
                    lignes.append(f"{role} : {texte}")
        historique = "\n".join(lignes) if lignes else "(aucun echange precedent)"

        # Retrouver le contexte -> vraie recherche Odoo
        contexte_filtre = self._extract_context_filter(question, historique)
        periods = contexte_filtre.get('periods') or []
        if periods:
            moves = self.env['account.move'].browse()
            for p in periods:
                moves |= self._search_moves(
                    contexte_filtre, p.get('date_from'), p.get('date_to'))
        else:
            moves = self._search_moves(contexte_filtre)

        devise = self.env.company.currency_id.name or 'DT'
        fournisseurs = {}
        for m in moves:
            nom = m.partner_id.name or 'Inconnu'
            fournisseurs.setdefault(nom, {'nombre': 0, 'total': 0.0})
            fournisseurs[nom]['nombre'] += 1
            fournisseurs[nom]['total'] += m.amount_total

        donnees_reelles = {
            'nombre_total': len(moves),
            'total_montant': round(sum(moves.mapped('amount_total')), 3),
            'devise': devise,
            'fournisseurs': [
                {'fournisseur': n, 'nombre': d['nombre'], 'total': round(d['total'], 3)}
                for n, d in fournisseurs.items()
            ],
        }

        prompt = f"""Tu es un assistant comptable amical et professionnel.

Historique de la conversation :
{historique}

L'utilisateur pose cette question : "{question}"

Voici les VRAIES donnees issues de la base (utilise UNIQUEMENT ces donnees,
n'invente jamais de fournisseur ou de chiffre) :
{json.dumps(donnees_reelles, ensure_ascii=False)}

Reponds directement et precisement a la question, en francais, court (2-3 phrases).
- Si la question porte sur "quel client/fournisseur", cite les vrais noms
- S'il y a plusieurs fournisseurs, dis-le et cite-les
- Si les donnees ne permettent pas de repondre, dis-le honnetement
- INTERDIT : ne dessine jamais de tableau en texte (pas de | ni ---),
  pas de markdown, uniquement des phrases naturelles
- Jamais de JSON ni de terme technique
"""
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        return {"answer": response.choices[0].message.content.strip(), "table": None}

    def _extract_context_filter(self, question, historique):
        client = self.env['invoice.ocr.groq'].get_client()

        today = datetime.today().strftime('%Y-%m-%d')

        prompt = f"""A partir de l'historique d'une conversation comptable,
deduis le filtre de recherche qui correspond au contexte actuel.

Date d'aujourd'hui : {today}

Historique :
{historique}

Question actuelle : "{question}"

Reponds UNIQUEMENT en JSON :
{{
    "partner_name": "nom du fournisseur concerne dans le contexte, ou vide si tous",
    "document_type": "facture" | "ticket" | "tous",
    "periods": [
        {{"label": "2025", "date_from": "2025-01-01", "date_to": "2025-12-31"}}
    ]
}}

Regles :
- Reprends le fournisseur et les periodes evoques dans l'historique
- Si plusieurs periodes ont ete comparees, mets-les toutes dans "periods"
- Si aucune periode precise, mets "periods": []
- Ne mets jamais de texte hors du JSON
"""
        try:
            try:
                response = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                )
            except Exception:
                response = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                )
            text = response.choices[0].message.content.strip()
            text = re.sub(r'^```(?:json)?|```$', '', text,
                          flags=re.MULTILINE).strip()
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                text = match.group()
            return json.loads(text)
        except Exception:
            return {"partner_name": "", "document_type": "tous", "periods": []}
