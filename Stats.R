library(DBI)
library(magrittr)
con <-dbConnect(RSQLite::SQLite(), ".genderify.db")

dbListTables(con)

dbReadTable(con, "artists") %>% View

dbReadTable(con, "artists")$gender %>% table %>% plot
