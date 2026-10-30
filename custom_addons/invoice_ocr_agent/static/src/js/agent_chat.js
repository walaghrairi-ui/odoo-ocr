/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, useRef, onMounted } from "@odoo/owl";
import { loadJS } from "@web/core/assets";

export class ChatAgent extends Component {
    setup() {
        this.orm = useService("orm");
        this.messagesRef = useRef("messages");
        this.inputRef = useRef("input");

        this.state = useState({
            messages: [],
            currentInput: "",
            loading: false,
            conversations: [],       // liste dans la barre laterale
            activeConversationId: null,
            // Popup de confirmation personnalisee
            confirmOpen: false,
            confirmTitle: "",
            confirmBody: "",
            confirmLabel: "Supprimer",
            confirmAction: null,
            // Renommage d'une conversation
            renamingId: null,
            renameValue: "",
        });

        this.suggestions = [
            "Combien de factures ai-je ce mois-ci ?",
            "Quelles factures arrivent a echeance cette semaine ?",
            "Compare mes factures de 2025 et 2026 en tableau",
            "Y a-t-il des factures sans compte comptable ?",
        ];

        onMounted(async () => {
            await this.loadConversations();
            if (this.inputRef.el) {
                this.inputRef.el.focus();
            }
        });
    }

    // ============================================================
    //  Barre laterale : charger / ouvrir / supprimer
    // ============================================================
    async loadConversations() {
        try {
            this.state.conversations = await this.orm.call(
                "invoice.agent.conversation",
                "get_my_conversations",
                []
            );
        } catch (e) {
            this.state.conversations = [];
        }
    }

    async openConversation(convId) {
        if (this.state.loading) {
            return;
        }
        this.state.activeConversationId = convId;
        this.state.messages = [];
        try {
            const msgs = await this.orm.call(
                "invoice.agent.conversation",
                "get_messages",
                [convId]
            );
            for (const m of msgs) {
                this.state.messages.push({
                    role: m.role,
                    text: m.text,
                    table: m.table || null,
                    chart: m.chart || null,
                    kpis: m.kpis || null,
                });
            }
            // redessiner les graphiques presents
            this.state.messages.forEach((m, idx) => {
                if (m.chart) {
                    this.drawChart(idx, m.chart);
                }
            });
        } catch (e) {
            // silencieux
        }
        this.scrollToBottom();
    }

    // Clic sur la poubelle -> ouvre la popup personnalisee
    deleteConversation(convId, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        this.state.confirmTitle = "Supprimer la conversation";
        this.state.confirmBody = "Voulez-vous vraiment supprimer cette conversation ? Cette action est definitive.";
        this.state.confirmLabel = "Supprimer";
        this.state.confirmAction = () => this._doDeleteConversation(convId);
        this.state.confirmOpen = true;
    }

    async _doDeleteConversation(convId) {
        try {
            await this.orm.call(
                "invoice.agent.conversation",
                "delete_conversation",
                [convId]
            );
        } catch (e) {
            // silencieux
        }
        if (this.state.activeConversationId === convId) {
            this.state.activeConversationId = null;
            this.state.messages = [];
        }
        await this.loadConversations();
    }

    // Clic sur "Effacer l'historique" -> ouvre la popup personnalisee
    deleteAllHistory() {
        this.state.confirmTitle = "Effacer tout l'historique";
        this.state.confirmBody = "Voulez-vous vraiment effacer TOUTES vos conversations ? Cette action est definitive et irreversible.";
        this.state.confirmLabel = "Tout effacer";
        this.state.confirmAction = () => this._doDeleteAllHistory();
        this.state.confirmOpen = true;
    }

    async _doDeleteAllHistory() {
        try {
            await this.orm.call(
                "invoice.agent.conversation",
                "delete_all_my_conversations",
                []
            );
        } catch (e) {
            // silencieux
        }
        this.state.activeConversationId = null;
        this.state.messages = [];
        await this.loadConversations();
    }

    // Confirmer / annuler la popup
    confirmYes() {
        const action = this.state.confirmAction;
        this.state.confirmOpen = false;
        if (action) {
            action();
        }
    }

    confirmNo() {
        this.state.confirmOpen = false;
        this.state.confirmAction = null;
    }

    // ============================================================
    //  Renommage d'une conversation
    // ============================================================
    startRename(conv, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        this.state.renamingId = conv.id;
        this.state.renameValue = conv.name;
    }

    async saveRename(convId, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        const name = (this.state.renameValue || "").trim();
        if (name) {
            try {
                await this.orm.call(
                    "invoice.agent.conversation",
                    "rename_conversation",
                    [convId, name]
                );
            } catch (e) {
                // silencieux
            }
        }
        this.state.renamingId = null;
        this.state.renameValue = "";
        await this.loadConversations();
    }

    cancelRename(ev) {
        if (ev) {
            ev.stopPropagation();
        }
        this.state.renamingId = null;
        this.state.renameValue = "";
    }

    onRenameKeydown(ev, convId) {
        if (ev.key === "Enter") {
            this.saveRename(convId);
        } else if (ev.key === "Escape") {
            this.cancelRename();
        }
    }

    // ============================================================
    //  Utilitaires
    // ============================================================
    // ============================================================
    //  Actions de l'agent (confirmation explicite requise)
    // ============================================================
    // Cocher / decocher un document
    toggleDoc(msgIndex, docId) {
        const msg = this.state.messages[msgIndex];
        if (!msg || msg.actionState !== "pending") {
            return;
        }
        const i = msg.selectedIds.indexOf(docId);
        if (i === -1) {
            msg.selectedIds.push(docId);
        } else {
            msg.selectedIds.splice(i, 1);
        }
    }

    isSelected(msg, docId) {
        return msg.selectedIds && msg.selectedIds.includes(docId);
    }

    // Tout cocher / tout decocher
    toggleAllDocs(msgIndex) {
        const msg = this.state.messages[msgIndex];
        if (!msg || msg.actionState !== "pending") {
            return;
        }
        const docs = msg.pendingAction.documents || [];
        if (msg.selectedIds.length === docs.length) {
            msg.selectedIds = [];
        } else {
            msg.selectedIds = docs.map((d) => d.id);
        }
    }

    allSelected(msg) {
        const docs = (msg.pendingAction && msg.pendingAction.documents) || [];
        return docs.length > 0 && msg.selectedIds.length === docs.length;
    }

    // Total des documents actuellement selectionnes
    selectedTotal(msg) {
        const docs = (msg.pendingAction && msg.pendingAction.documents) || [];
        const somme = docs
            .filter((d) => msg.selectedIds.includes(d.id))
            .reduce((acc, d) => acc + (d.montant_brut || 0), 0);
        return this.formatNumber(somme);
    }

    // Libelle dynamique du bouton de confirmation.
    // Les mots viennent de l'action elle-meme (verbe + nom de l'element),
    // ce qui evite d'ecrire un cas par action dans le JavaScript.
    confirmLabel(msg) {
        const pa = msg.pendingAction || {};
        const n = msg.selectedIds ? msg.selectedIds.length : 0;
        if (n === 0) {
            return pa.vide_label || "Aucun document selectionne";
        }
        const verbe = pa.verbe || "Valider";
        const nom = pa.nom_item || "document";
        return verbe + " " + n + " " + nom + (n > 1 ? "s" : "");
    }

    async runAction(msgIndex) {
        const msg = this.state.messages[msgIndex];
        if (!msg || !msg.pendingAction || msg.actionState !== "pending") {
            return;
        }
        // On n'envoie que les documents explicitement coches
        const ids = (msg.selectedIds || []).slice();
        if (ids.length === 0) {
            return;
        }
        msg.actionState = "running";
        try {
            const res = await this.orm.call(
                "invoice.ocr.agent",
                "execute_action",
                [msg.pendingAction.type, ids]
            );
            msg.actionState = res.success ? "done" : "failed";

            const suivi = {
                role: "assistant",
                text: res.message || "Action terminee.",
                table: null,
                chart: null,
                kpis: null,
                actionResult: res.success ? "success" : "failed",
            };
            this.state.messages.push(suivi);

            if (this.state.activeConversationId) {
                this.orm.call("invoice.agent.conversation", "save_message",
                    [this.state.activeConversationId, "assistant", suivi.text])
                    .catch(() => {});
            }
        } catch (e) {
            msg.actionState = "failed";
            this.state.messages.push({
                role: "assistant",
                text: "L'action n'a pas pu etre executee. Veuillez reessayer.",
                table: null,
            });
        }
        this.scrollToBottom();
    }

    dismissAction(msgIndex) {
        const msg = this.state.messages[msgIndex];
        if (!msg || msg.actionState !== "pending") {
            return;
        }
        msg.actionState = "cancelled";
        const suivi = {
            role: "assistant",
            text: (msg.pendingAction && msg.pendingAction.texte_annule)
                || "Tres bien, aucune modification n'a ete effectuee. "
                   + "Les documents restent en brouillon.",
            table: null,
        };
        this.state.messages.push(suivi);
        if (this.state.activeConversationId) {
            this.orm.call("invoice.agent.conversation", "save_message",
                [this.state.activeConversationId, "assistant", suivi.text])
                .catch(() => {});
        }
        this.scrollToBottom();
    }

    // Intitule du bloc graphique selon son type
    chartHeading(chart) {
        if (!chart) {
            return "Visualisation graphique";
        }
        if (chart.type === "pie") {
            return "Repartition";
        }
        if (chart.type === "line") {
            return "Evolution";
        }
        return "Comparaison visuelle";
    }

    formatNumber(value) {
        if (value === null || value === undefined) {
            return "0";
        }
        const parts = Number(value).toFixed(2).split(".");
        parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, " ");
        if (parts[1] === "00") {
            return parts[0];
        }
        return parts[0] + "," + parts[1];
    }

    onKeydown(ev) {
        if (ev.key === "Enter" && !this.state.loading) {
            this.sendMessage();
        }
    }

    sendSuggestion(text) {
        this.state.currentInput = text;
        this.sendMessage();
    }

    // ============================================================
    //  Envoi d'un message
    // ============================================================
    async sendMessage() {
        const question = (this.state.currentInput || "").trim();
        if (!question || this.state.loading) {
            return;
        }

        // Creer une conversation si aucune active
        let isNewConv = false;
        if (!this.state.activeConversationId) {
            try {
                this.state.activeConversationId = await this.orm.call(
                    "invoice.agent.conversation",
                    "create_conversation",
                    [question]
                );
                isNewConv = true;
            } catch (e) {
                // continue meme si echec
            }
        }

        this.state.messages.push({ role: "user", text: question, table: null });
        this.state.currentInput = "";
        this.state.loading = true;
        this.scrollToBottom();

        // Sauvegarder le message utilisateur
        if (this.state.activeConversationId) {
            this.orm.call("invoice.agent.conversation", "save_message",
                [this.state.activeConversationId, "user", question]).catch(() => {});
        }

        try {
            const history = this.state.messages
                .slice(-7, -1)
                .map((m) => ({ role: m.role, text: m.text }));

            const result = await this.orm.call(
                "invoice.ocr.agent",
                "ask_agent",
                [question, history]
            );

            const answer = result.answer || "Je n'ai pas de reponse.";
            const table = result.table || null;
            const chart = result.chart || null;
            const kpis = result.kpis || null;
            const pending = result.pending_action || null;

            this.state.messages.push({
                role: "assistant",
                text: answer,
                table: table,
                chart: chart,
                kpis: kpis,
                pendingAction: pending,
                actionState: pending ? "pending" : null,
                // Tous les documents sont selectionnes par defaut
                selectedIds: pending
                    ? (pending.documents || []).map((d) => d.id)
                    : [],
            });

            if (chart) {
                const msgIndex = this.state.messages.length - 1;
                this.drawChart(msgIndex, chart);
            }

            // Sauvegarder la reponse de l'assistant
            if (this.state.activeConversationId) {
                this.orm.call("invoice.agent.conversation", "save_message",
                    [this.state.activeConversationId, "assistant", answer, table, chart, kpis])
                    .catch(() => {});
            }

            // Rafraichir la liste si nouvelle conversation
            if (isNewConv) {
                await this.loadConversations();
            }
        } catch (error) {
            this.state.messages.push({
                role: "assistant",
                text: "Desole, je n'ai pas pu traiter votre demande pour le moment. " +
                    "Verifiez votre connexion internet, puis reessayez ou reformulez votre question.",
                table: null,
            });
        } finally {
            this.state.loading = false;
            this.scrollToBottom();
            if (this.inputRef.el) {
                this.inputRef.el.focus();
            }
        }
    }

    // Nouveau chat (vide la zone, sans supprimer l'historique)
    clearChat() {
        this.state.messages = [];
        this.state.currentInput = "";
        this.state.activeConversationId = null;
        if (this.inputRef.el) {
            this.inputRef.el.focus();
        }
    }

    scrollToBottom() {
        setTimeout(() => {
            if (this.messagesRef.el) {
                this.messagesRef.el.scrollTop = this.messagesRef.el.scrollHeight;
            }
        }, 50);
    }

    // ============================================================
    //  Graphique
    // ============================================================
    async drawChart(msgIndex, chartData) {
        await loadJS("/web/static/lib/Chart/Chart.js");

        setTimeout(() => {
            const canvas = document.getElementById("agentChart_" + msgIndex);
            if (!canvas) {
                return;
            }

            const devise = chartData.currency || "DT";
            const ctx = canvas.getContext("2d");
            const type = chartData.type || "bar";

            // Palette premium (couleurs pleines + degrades)
            const solides = [
                "#6366f1", "#14b8a6", "#f59e0b", "#ec4899",
                "#22c55e", "#0ea5e9", "#8b5cf6", "#ef4444",
                "#eab308", "#06b6d4",
            ];
            const duos = [
                ["#6366f1", "#8b5cf6"],
                ["#14b8a6", "#0ea5e9"],
                ["#f59e0b", "#ef4444"],
                ["#ec4899", "#8b5cf6"],
                ["#22c55e", "#14b8a6"],
            ];

            const fmt = (v) => new Intl.NumberFormat("fr-FR").format(Math.round(v));

            // Degrades verticaux (barres)
            const degradesBarres = chartData.amounts.map((v, i) => {
                const duo = duos[i % duos.length];
                const g = ctx.createLinearGradient(0, 0, 0, canvas.height || 300);
                g.addColorStop(0, duo[0]);
                g.addColorStop(1, duo[1]);
                return g;
            });

            // Plugin : valeur affichee au-dessus de chaque barre
            const etiquettesValeurs = {
                id: "etiquettesValeurs",
                afterDatasetsDraw(chart) {
                    const c = chart.ctx;
                    c.save();
                    chart.getDatasetMeta(0).data.forEach((el, i) => {
                        const v = chart.data.datasets[0].data[i];
                        c.font = "600 12px Inter, sans-serif";
                        c.fillStyle = "#374151";
                        c.textAlign = "center";
                        c.fillText(fmt(v) + " " + devise, el.x, el.y - 8);
                    });
                    c.restore();
                },
            };

            const tooltipPremium = {
                backgroundColor: "#111827",
                padding: 14,
                cornerRadius: 10,
                titleFont: { size: 13, weight: "700" },
                bodyFont: { size: 13 },
                displayColors: type === "pie",
            };

            // ================= CAMEMBERT (repartition) =================
            if (type === "pie") {
                const total = chartData.amounts.reduce((a, b) => a + b, 0) || 1;
                // eslint-disable-next-line no-undef
                new Chart(canvas, {
                    type: "doughnut",
                    data: {
                        labels: chartData.labels,
                        datasets: [{
                            data: chartData.amounts,
                            backgroundColor: chartData.amounts.map(
                                (v, i) => solides[i % solides.length]),
                            borderColor: "#ffffff",
                            borderWidth: 3,
                            hoverOffset: 14,
                        }],
                    },
                    plugins: [{
                        id: "partsPourcentage",
                        afterDatasetsDraw(chart) {
                            const c = chart.ctx;
                            c.save();
                            chart.getDatasetMeta(0).data.forEach((arc, i) => {
                                const v = chart.data.datasets[0].data[i];
                                const pct = (v / total) * 100;
                                if (pct < 6) { return; }  // trop petit : illisible
                                const p = arc.tooltipPosition();
                                c.font = "700 12px Inter, sans-serif";
                                c.fillStyle = "#ffffff";
                                c.textAlign = "center";
                                c.textBaseline = "middle";
                                c.shadowColor = "rgba(0,0,0,0.35)";
                                c.shadowBlur = 3;
                                c.fillText(pct.toFixed(0) + "%", p.x, p.y);
                            });
                            c.restore();
                        },
                    }],
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        cutout: "58%",
                        animation: { duration: 1200, easing: "easeOutQuart" },
                        plugins: {
                            legend: {
                                display: true,
                                position: "right",
                                labels: {
                                    boxWidth: 14,
                                    boxHeight: 14,
                                    usePointStyle: true,
                                    pointStyle: "circle",
                                    padding: 14,
                                    color: "#374151",
                                    font: { size: 12, weight: "600" },
                                },
                            },
                            tooltip: Object.assign({}, tooltipPremium, {
                                callbacks: {
                                    label: function (c) {
                                        const pct = ((c.parsed / total) * 100).toFixed(1);
                                        return "  " + fmt(c.parsed) + " " + devise
                                               + "  (" + pct + "%)";
                                    },
                                },
                            }),
                        },
                    },
                });
                return;
            }

            // ================= COURBE (evolution) =================
            if (type === "line") {
                const remplissage = ctx.createLinearGradient(0, 0, 0, canvas.height || 300);
                remplissage.addColorStop(0, "rgba(99, 102, 241, 0.35)");
                remplissage.addColorStop(1, "rgba(99, 102, 241, 0.02)");
                // eslint-disable-next-line no-undef
                new Chart(canvas, {
                    type: "line",
                    data: {
                        labels: chartData.labels,
                        datasets: [{
                            data: chartData.amounts,
                            borderColor: "#6366f1",
                            borderWidth: 3,
                            backgroundColor: remplissage,
                            fill: true,
                            tension: 0.35,
                            pointBackgroundColor: "#ffffff",
                            pointBorderColor: "#6366f1",
                            pointBorderWidth: 3,
                            pointRadius: 5,
                            pointHoverRadius: 8,
                        }],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        layout: { padding: { top: 20 } },
                        animation: { duration: 1400, easing: "easeOutQuart" },
                        plugins: {
                            legend: { display: false },
                            tooltip: Object.assign({}, tooltipPremium, {
                                callbacks: {
                                    label: (c) => "  " + fmt(c.parsed.y) + " " + devise,
                                },
                            }),
                        },
                        scales: {
                            y: {
                                beginAtZero: true,
                                grid: { color: "#f1f1f4", drawBorder: false },
                                ticks: {
                                    color: "#9ca3af", font: { size: 11 },
                                    callback: (v) => fmt(v),
                                },
                            },
                            x: {
                                grid: { display: false, drawBorder: false },
                                ticks: { color: "#374151", font: { size: 12, weight: "600" } },
                            },
                        },
                    },
                });
                return;
            }

            // ================= BARRES (comparaison / classement) =================
            // eslint-disable-next-line no-undef
            new Chart(canvas, {
                type: "bar",
                data: {
                    labels: chartData.labels,
                    datasets: [{
                        label: "Montant total (" + devise + ")",
                        data: chartData.amounts,
                        backgroundColor: degradesBarres,
                        borderRadius: 12,
                        borderSkipped: false,
                        maxBarThickness: 100,
                        hoverBackgroundColor: degradesBarres,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    layout: { padding: { top: 28 } },
                    animation: { duration: 1400, easing: "easeOutQuart" },
                    plugins: {
                        legend: { display: false },
                        tooltip: Object.assign({}, tooltipPremium, {
                            callbacks: {
                                label: (c) => "  " + fmt(c.parsed.y) + " " + devise,
                            },
                        }),
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            grid: { color: "#f1f1f4", drawBorder: false },
                            ticks: {
                                color: "#9ca3af", font: { size: 11 },
                                callback: (v) => fmt(v),
                            },
                        },
                        x: {
                            grid: { display: false, drawBorder: false },
                            ticks: { color: "#374151", font: { size: 13, weight: "600" } },
                        },
                    },
                },
                plugins: [etiquettesValeurs],
            });
        }, 120);
    }
}

ChatAgent.template = "invoice_ocr_agent.ChatAgent";

registry.category("actions").add("invoice_ocr_agent.chat", ChatAgent);
