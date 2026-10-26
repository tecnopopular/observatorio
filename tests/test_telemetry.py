"""Testes do módulo de infraestrutura, telemetria de rede e agregação do Radar Cívico."""

import json
import tempfile
import unittest
from pathlib import Path

from observatorio.cli import build_parser
from observatorio.engine import generate_radar_artifacts
from observatorio.models import (
    AuditLevel,
    CivicAuditReport,
    DimensionScore,
    DimensionType,
    HostingProvider,
    PerformanceDetail,
    TelemetryInspection,
)
from observatorio.telemetry import (
    build_radar_dataset,
    detect_analytics,
    get_radar_timestamps,
    inspect_target_telemetry,
    resolve_hosting_provider,
)


class TestTelemetry(unittest.TestCase):
    """Bateria de testes de resolução de infraestrutura e detecção de rastreadores."""

    def test_resolve_hosting_provider(self):
        """Verifica a identificação precisa de provedores por servidor, cabeçalho e domínio."""
        self.assertEqual(
            resolve_hosting_provider("cloudflare", {}, "https://exemplo.org"),
            HostingProvider.CLOUDFLARE.value,
        )
        self.assertEqual(
            resolve_hosting_provider(None, {"cf-ray": "12345"}, "https://exemplo.org"),
            HostingProvider.CLOUDFLARE.value,
        )
        self.assertEqual(
            resolve_hosting_provider(None, {}, "https://minha-app.pages.dev"),
            HostingProvider.CLOUDFLARE.value,
        )

        self.assertEqual(
            resolve_hosting_provider("Vercel", {}, "https://exemplo.org"),
            HostingProvider.VERCEL.value,
        )
        self.assertEqual(
            resolve_hosting_provider(None, {"x-vercel-id": "gru1::123"}, "https://exemplo.org"),
            HostingProvider.VERCEL.value,
        )
        self.assertEqual(
            resolve_hosting_provider(None, {}, "https://minha-app.vercel.app"),
            HostingProvider.VERCEL.value,
        )

        self.assertEqual(
            resolve_hosting_provider("github.com", {}, "https://exemplo.org"),
            HostingProvider.GITHUB_PAGES.value,
        )
        self.assertEqual(
            resolve_hosting_provider(None, {}, "https://organizacao.github.io/projeto"),
            HostingProvider.GITHUB_PAGES.value,
        )

        self.assertEqual(
            resolve_hosting_provider("Netlify", {}, "https://exemplo.org"),
            HostingProvider.NETLIFY.value,
        )
        self.assertEqual(
            resolve_hosting_provider(None, {"x-nf-request-id": "abc"}, "https://exemplo.org"),
            HostingProvider.NETLIFY.value,
        )
        self.assertEqual(
            resolve_hosting_provider(None, {}, "https://minha-app.netlify.app"),
            HostingProvider.NETLIFY.value,
        )

        self.assertEqual(
            resolve_hosting_provider("AmazonS3", {}, "https://exemplo.org"),
            HostingProvider.AWS.value,
        )
        self.assertEqual(
            resolve_hosting_provider("CloudFront", {}, "https://exemplo.org"),
            HostingProvider.AWS.value,
        )
        self.assertEqual(
            resolve_hosting_provider(None, {}, "https://bucket.s3.amazonaws.com"),
            HostingProvider.AWS.value,
        )

        self.assertEqual(
            resolve_hosting_provider("nginx/1.24.0", {}, "https://exemplo.org"),
            HostingProvider.SOVEREIGN_VPS.value,
        )
        self.assertEqual(
            resolve_hosting_provider("Apache/2.4.52 (Ubuntu)", {}, "https://exemplo.org"),
            HostingProvider.SOVEREIGN_VPS.value,
        )
        self.assertEqual(
            resolve_hosting_provider("Caddy", {}, "https://exemplo.org"),
            HostingProvider.SOVEREIGN_VPS.value,
        )

        self.assertEqual(
            resolve_hosting_provider("nginx", {}, "https://exemplo.org", is_online=False),
            HostingProvider.INACTIVE.value,
        )
        self.assertEqual(
            resolve_hosting_provider("ProvedorDesconhecido", {}, "https://exemplo.org"),
            HostingProvider.OTHER.value,
        )

    def test_detect_analytics_clean(self):
        """Assegura que páginas sem scripts comerciais ou de telemetria retornem listas vazias."""
        html = "<html><body><h1>Projeto Cívico</h1><p>Sem rastreadores</p></body></html>"
        all_dets, details, comm, eth = detect_analytics(html)
        self.assertEqual(all_dets, [])
        self.assertEqual(details, {})
        self.assertEqual(comm, [])
        self.assertEqual(eth, [])

    def test_detect_analytics_commercial(self):
        """Assegura a identificação precisa de GA4, Meta Pixel e Hotjar/Clarity."""
        html = """
        <html>
        <head>
          <script async src="https://www.googletagmanager.com/gtag/js?id=G-XYZ12345"></script>
          <script>
            fbq('init', '1234567890');
          </script>
          <script src="https://www.clarity.ms/tag/abc999"></script>
        </head>
        <body>Conteudo</body>
        </html>
        """
        all_dets, details, comm, eth = detect_analytics(html)
        self.assertEqual(len(all_dets), 3)
        self.assertIn("Google Analytics (GA4/GTM)", comm)
        self.assertIn("Meta Pixel (Facebook)", comm)
        self.assertIn("Hotjar / Clarity (Mapas de Calor)", comm)
        self.assertEqual(eth, [])
        self.assertEqual(details["Google Analytics (GA4/GTM)"], ["G-XYZ12345"])
        self.assertEqual(details["Meta Pixel (Facebook)"], ["1234567890"])
        self.assertEqual(details["Hotjar / Clarity (Mapas de Calor)"], ["abc999"])

    def test_detect_analytics_ethical(self):
        """Assegura o reconhecimento de ferramentas éticas e focadas em privacidade."""
        html = """
        <html>
        <head>
          <script defer data-domain="meusite.org" src="https://plausible.io/js/script.js"></script>
          <script defer src="https://static.cloudflareinsights.com/beacon.min.js"></script>
        </head>
        <body>Conteudo</body>
        </html>
        """
        all_dets, details, comm, eth = detect_analytics(html)
        self.assertEqual(len(all_dets), 2)
        self.assertEqual(comm, [])
        self.assertIn("Plausible Analytics", eth)
        self.assertIn("Cloudflare Web Analytics", eth)
        self.assertIn("meusite.org", details["Plausible Analytics"])

    def test_inspect_target_telemetry(self):
        """Garante a construção íntegra do objeto TelemetryInspection."""
        tel = inspect_target_telemetry(
            nome="Iniciativa Teste",
            url="https://iniciativa.pages.dev",
            tool_id="iniciativa-teste",
            categoria="Transparência",
            status_code=200,
            server="cloudflare",
            headers={"server": "cloudflare"},
            raw_html="<html><body><script src='https://static.cloudflareinsights.com/beacon.min.js'></script></body></html>",
        )
        self.assertTrue(tel.is_online)
        self.assertEqual(tel.hosting_provider, HostingProvider.CLOUDFLARE.value)
        self.assertEqual(tel.analytics_detected, ["Cloudflare Web Analytics"])
        self.assertEqual(tel.status_code, 200)

    def test_get_radar_timestamps(self):
        """Valida que timestamps em BRT e ISO sejam formatados corretamente."""
        iso_str, date_str, dt_str = get_radar_timestamps()
        self.assertTrue(iso_str)
        self.assertIn("202", date_str)
        self.assertIn("(BRT)", dt_str)

    def test_build_radar_dataset(self):
        """Testa o cálculo agregado de métricas, distribuição e dimensões do radar."""
        dim_score = DimensionScore(
            dimension=DimensionType.ACESSIBILIDADE_DESEMPENHO,
            weight_percentage=15.0,
            raw_score=90.0,
            weighted_score=13.5,
            is_passing=True,
            findings=[],
            recommendations=[],
        )
        report = CivicAuditReport(
            tool_id="ferramenta-alfa",
            tool_name="Ferramenta Alfa",
            target_url="https://alfa.org",
            status=AuditLevel.AUDITADO,
            total_score=85.0,
            badge_label="Certificado",
            dimension_scores={
                DimensionType.ACESSIBILIDADE_DESEMPENHO: dim_score,
                DimensionType.PRIVACIDADE_SOBERANIA: dim_score,
                DimensionType.PROVENIENCIA_DADOS: dim_score,
                DimensionType.CODIGO_ABERTO_FLOSS: dim_score,
                DimensionType.ENQUADRAMENTO_REPUBLICANO: dim_score,
            },
            performance=PerformanceDetail(server="nginx/1.24", http_status=200),
        )
        tel = TelemetryInspection(
            nome="Ferramenta Alfa",
            url="https://alfa.org",
            tool_id="ferramenta-alfa",
            categoria="Estatística",
            status_code=200,
            server="nginx/1.24",
            hosting_provider=HostingProvider.SOVEREIGN_VPS.value,
            is_online=True,
            analytics_detected=[],
        )
        target = {
            "nome": "Ferramenta Alfa",
            "url": "https://alfa.org",
            "_tool_id": "ferramenta-alfa",
            "categoria": "Estatística",
            "descricao": "Descrição de teste",
        }

        dataset = build_radar_dataset([report], [tel], [target])

        self.assertIn("metadata", dataset)
        self.assertIn("summary", dataset)
        self.assertIn("iniciativas", dataset)

        summary = dataset["summary"]
        self.assertEqual(summary["total_iniciativas"], 1)
        self.assertEqual(summary["certified_count"], 1)
        self.assertEqual(summary["certified_pct"], 100.0)
        self.assertEqual(summary["cataloged_count"], 0)
        self.assertEqual(summary["online_count"], 1)
        self.assertEqual(summary["online_pct"], 100.0)

        infra_item = summary["infra_breakdown"][0]
        self.assertEqual(infra_item["name"], HostingProvider.SOVEREIGN_VPS.value)
        self.assertEqual(infra_item["count"], 1)

        tracker_item = summary["trackers_breakdown"][0]
        self.assertIn("Zero Trackers", tracker_item["name"])
        self.assertEqual(tracker_item["count"], 1)
        self.assertTrue(tracker_item["is_clean"])

        dim_metrics = summary["dimensions"]
        self.assertIn("acessibilidade_desempenho", dim_metrics)
        self.assertEqual(dim_metrics["acessibilidade_desempenho"]["passing_count"], 1)
        self.assertEqual(dim_metrics["acessibilidade_desempenho"]["passing_pct"], 100.0)

    def test_generate_radar_artifacts_end_to_end(self):
        """Valida a orquestração completa gerando os arquivos JSON de radar e telemetria."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cat_file = tmp_path / "catalogo.json"
            audit_file = tmp_path / "laudos.json"
            radar_file = tmp_path / "radar.json"
            tel_file = tmp_path / "telemetria.json"
            cache_dir = tmp_path / "cache"
            cache_dir.mkdir(parents=True)

            cat_data = [
                {
                    "nome": "Radar Beta",
                    "url": "https://beta.pages.dev",
                    "categoria": "Transparência",
                }
            ]
            cat_file.write_text(json.dumps(cat_data), encoding="utf-8")

            (cache_dir / "radar-beta.html").write_text(
                "<html><body>Conteúdo sem trackers</body></html>", encoding="utf-8"
            )
            (cache_dir / "radar-beta.meta.json").write_text(
                json.dumps(
                    {
                        "server": "cloudflare",
                        "status_code": 200,
                        "headers": {"server": "cloudflare"},
                    }
                ),
                encoding="utf-8",
            )

            dim_score = DimensionScore(
                dimension=DimensionType.ACESSIBILIDADE_DESEMPENHO,
                weight_percentage=15.0,
                raw_score=85.0,
                weighted_score=12.75,
                is_passing=True,
                findings=[],
                recommendations=[],
            )
            audit_data = [
                CivicAuditReport(
                    tool_id="radar-beta",
                    tool_name="Radar Beta",
                    target_url="https://beta.pages.dev",
                    status=AuditLevel.CATALOGADO,
                    total_score=75.0,
                    badge_label="Catalogado",
                    dimension_scores={
                        DimensionType.ACESSIBILIDADE_DESEMPENHO: dim_score,
                        DimensionType.PRIVACIDADE_SOBERANIA: dim_score,
                        DimensionType.PROVENIENCIA_DADOS: dim_score,
                        DimensionType.CODIGO_ABERTO_FLOSS: dim_score,
                        DimensionType.ENQUADRAMENTO_REPUBLICANO: dim_score,
                    },
                ).model_dump(mode="json")
            ]
            audit_file.write_text(json.dumps(audit_data), encoding="utf-8")

            dataset = generate_radar_artifacts(
                input_file=cat_file,
                audits_file=audit_file,
                radar_file=radar_file,
                telemetry_file=tel_file,
                cache_dir=cache_dir,
            )

            self.assertTrue(radar_file.exists())
            self.assertTrue(tel_file.exists())
            self.assertEqual(dataset["summary"]["total_iniciativas"], 1)

            saved_tel = json.loads(tel_file.read_text(encoding="utf-8"))
            self.assertEqual(len(saved_tel), 1)
            self.assertEqual(saved_tel[0]["hosting_provider"], HostingProvider.CLOUDFLARE.value)

    def test_cli_flags_and_subcommand(self):
        """Verifica se os comandos de CLI para radar estão devidamente expostos."""
        parser = build_parser()

        args_audit = parser.parse_args(["audit-all", "--no-radar"])
        self.assertTrue(args_audit.no_radar)

        args_radar = parser.parse_args(["gerar-radar", "--input", "cat.json"])
        self.assertEqual(args_radar.command, "gerar-radar")
        self.assertEqual(args_radar.input, "cat.json")


if __name__ == "__main__":
    unittest.main()
