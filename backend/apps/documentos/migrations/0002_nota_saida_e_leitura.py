"""Sprint 4 fase 2 — a leitura que se confere sozinha.

Três alterações de metadado, nenhuma de dado: entra o tipo `nota_saida` (a chave
de acesso diz quem emitiu, e isso separa receita de despesa sem ninguém opinar) e
os textos de ajuda de `confianca`/`dados_extraidos` deixam de falar em "enquanto
não há OCR". Nada a migrar nas linhas existentes: documento antigo continua com
confiança zero, que é a verdade sobre ele — ninguém provou nada a seu respeito.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='documento',
            name='confianca',
            field=models.PositiveSmallIntegerField(default=0, help_text='0 a 100. Só passa do limiar o que se confere sozinho: XML assinado ou número com dígito verificador. Abaixo dele o documento espera humano — é o gate do Sprint 4.'),
        ),
        migrations.AlterField(
            model_name='documento',
            name='dados_extraidos',
            field=models.JSONField(blank=True, default=dict, help_text='Chave de acesso, CNPJ emitente, competência, linha digitável, valor, vencimento — mais o método que leu. Vazio quando ninguém conseguiu provar nada sobre o arquivo.'),
        ),
        migrations.AlterField(
            model_name='documento',
            name='tipo',
            field=models.CharField(choices=[('desconhecido', 'A classificar'), ('nota_entrada', 'Nota de entrada'), ('nota_saida', 'Nota de saída'), ('nota_servico', 'Nota de serviço'), ('boleto', 'Boleto'), ('extrato', 'Extrato bancário'), ('contrato', 'Contrato'), ('outro', 'Outro')], default='desconhecido', max_length=20),
        ),
    ]
