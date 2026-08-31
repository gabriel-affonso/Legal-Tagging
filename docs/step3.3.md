# Step 3.3 — Evidência Cadastral e Chave Matricial

O Step 3.3 trata o proprietário como **titular cadastral**, não como um
sinónimo de senhorio ou arrendatário. Para cada caderneta encontrada no mesmo
PDF, a pipeline mantém juntos artigo, secção, páginas de origem e o bloco de
titulares antes de publicar qualquer campo.

## Publicação

- `property_matrix_key` é derivada deterministicamente de artigo e secção,
  por exemplo `80-J`; ela não é gerada pelo LLM.
- `owner_name` só é publicado quando a caderneta tem estrutura verificável e
  o nome aparece no seu bloco de titulares.
- Rótulos como `Arrendatário`, `Senhorio` e `Titulares` não são nomes de
  proprietário.
- Quando existem várias cadernetas, a seleção exige coincidência exata da
  chave matricial; na ausência de uma escolha única, o resultado vai para
  revisão, sem atribuir proprietário.

O proprietário contratual (`lessor`) continua separado do proprietário
cadastral (`owner_name`).
